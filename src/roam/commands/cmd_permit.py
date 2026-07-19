"""``roam permit`` — structural-permission verdict facade + W198 persistence.

This module exposes ``roam permit`` as a Click group that defaults to the
historical verdict-facade behaviour (ALLOW / REVIEW / BLOCK over a diff
or symbol) when no subcommand is supplied, AND adds a real
``roam permit issue --persist`` subcommand that writes a stable
``permit_id`` to ``.roam/permits/<id>.json`` (W198).

W198 — closing the facade gap
=============================

Until W198, ``roam permit`` was strictly a verdict facade: no
``permit_id`` was ever persisted. The W182
``AuthorityRef(authority_kind="permit")`` slot in
:class:`roam.evidence.change_evidence.ChangeEvidence` therefore had
nothing to bind to in production -- the W268
``_load_permits_from_disk`` reader scanned ``.roam/permits/`` but no
command wrote rows there. W198 ships the writer:

* ``roam permit issue --persist --scope X --expires-at Y --issued-to Z``
  writes ``.roam/permits/<permit_id>.json`` via
  :func:`roam.atomic_io.atomic_write_json`.
* When an active run is in flight, the issuance event auto-logs with
  ``permit_id`` stamped on the run-ledger event -- the W292 collector
  harvester then promotes the matching ``AuthorityRef`` to
  ``provenance="run_ledger"`` (W294 corroboration channel).
* Without ``--persist``, ``roam permit issue`` is a dry-run that prints
  the synthesised id but writes nothing -- backward compat with the
  pre-W198 verdict-facade-only world.

Verdict-facade surface (default path)
======================================

When ``roam permit`` is invoked without a subcommand, the historical
verdict-facade engine runs unchanged: it wraps ``roam critique`` +
``roam preflight`` and emits an ALLOW / REVIEW / BLOCK verdict-shaped
JSON envelope. This is the path Cursor rules / Claude Code permission
hooks / pre-commit gates rely on; the W198 wave kept it byte-stable.

    {
      "verdict": "ALLOW" | "REVIEW" | "BLOCK",
      "reason": "short human-readable explanation",
      "allowed_actions": ["commit", "merge", "push"],
      "blocked_actions": ["force-push", "auto-merge"],
      "evidence": { "...": ... }
    }

Exit codes (verdict-facade path):

* 0 -- verdict ALLOW (or no risk surfaced)
* 5 -- verdict BLOCK (``EXIT_GATE_FAILURE``)
* 6 -- verdict REVIEW (``EXIT_PARTIAL``) -- reviewer should look but not blocked

The verdict-facade path remains a stand-alone engine reusable from any
PR-comment renderer or pre-commit hook.

W198 vocabulary note
====================

The verdict-facade and the W198 issuance subcommand are two distinct
surfaces sharing one command name. The facade answers "is this change
allowed RIGHT NOW given the analysis signals?"; ``issue --persist``
answers "record that authority Z granted scope X to identity Y until
expiry W". Both feed the agentic-assurance crosswalk's *authority*
axis; the facade reports the structural-check verdict, the issuance
records the human-or-policy override.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam permit`` operates on substrate state in ``.roam/``
(permission records) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys
from pathlib import Path

import click
from click.testing import CliRunner

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root
from roam.exit_codes import EXIT_GATE_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS, EXIT_USAGE
from roam.output.formatter import format_table, json_envelope, to_json
from roam.permits import (
    PermitRecord,
    PermitRequest,
    issue_permit,
    list_permits,
    permits_root,
    read_permit,
)
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Verdict-facade helpers (unchanged from the pre-W198 facade)
# ---------------------------------------------------------------------------


def _capture_critique(diff_text: str) -> dict:
    """Invoke roam critique --json on the supplied diff (in-process via CliRunner)."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "critique"], input=diff_text, catch_exceptions=False)
    if result.exit_code not in (EXIT_SUCCESS, EXIT_GATE_FAILURE):
        return {"error": f"critique exited {result.exit_code}", "output": result.output[:200]}
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError:
        return {"error": "critique produced non-JSON output", "output": result.output[:200]}


def _capture_preflight(symbol: str) -> dict:
    """Invoke roam preflight --json on the supplied symbol."""
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "preflight", symbol], catch_exceptions=False)
    if result.exit_code not in (EXIT_SUCCESS, EXIT_PARTIAL, EXIT_GATE_FAILURE):
        return {"error": f"preflight exited {result.exit_code}"}
    try:
        return _json.loads(result.output)
    except _json.JSONDecodeError:
        return {"error": "preflight produced non-JSON output"}


def _acquire_staged_diff() -> str:
    """Return `git diff --cached` output, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _verdict_from_signals(
    critique_result: dict,
    preflight_result: dict | None,
    *,
    block_on_high_severity: int = 1,
    review_on_blast_radius: int = 60,
) -> dict:
    """Compose verdict + reason + allowed/blocked action lists from raw signals.

    Decision tree (most aggressive first):

    1. Any high-severity critique finding -> BLOCK
    2. Preflight risk == HIGH and blast radius > review threshold -> REVIEW
    3. Preflight risk == HIGH alone -> REVIEW
    4. Otherwise -> ALLOW
    """
    high_count = 0
    summary = critique_result.get("summary") or {}
    high_count = int(summary.get("high_severity_findings") or summary.get("high_severity_total") or 0)

    blast_radius = 0
    risk_level = ""
    if preflight_result:
        psumm = preflight_result.get("summary") or {}
        blast_radius = int(psumm.get("blast_radius") or psumm.get("blast_radius_count") or 0)
        risk_level = (psumm.get("risk_level") or psumm.get("overall_risk") or "").upper()

    if high_count >= block_on_high_severity:
        return {
            "verdict": "BLOCK",
            "reason": f"{high_count} high-severity critique finding(s) - fix before merging",
            "allowed_actions": ["edit", "split-pr"],
            "blocked_actions": ["commit", "push", "merge", "auto-merge"],
        }

    if risk_level == "HIGH" and blast_radius >= review_on_blast_radius:
        return {
            "verdict": "REVIEW",
            "reason": f"high blast radius ({blast_radius} symbols affected); reviewer should sign off before merge",
            "allowed_actions": ["commit", "request-review"],
            "blocked_actions": ["auto-merge", "force-push"],
        }

    if risk_level == "HIGH":
        return {
            "verdict": "REVIEW",
            "reason": "high preflight risk - manual review recommended",
            "allowed_actions": ["commit", "request-review"],
            "blocked_actions": ["auto-merge", "force-push"],
        }

    return {
        "verdict": "ALLOW",
        "reason": "no high-severity findings; safe to proceed",
        "allowed_actions": ["commit", "push", "merge"],
        "blocked_actions": [],
    }


def _exit_for_verdict(verdict: str) -> int:
    return {
        "BLOCK": EXIT_GATE_FAILURE,
        "REVIEW": EXIT_PARTIAL,
        "ALLOW": EXIT_SUCCESS,
    }.get(verdict, EXIT_SUCCESS)


# ---------------------------------------------------------------------------
# Click group + default callback (verdict facade)
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Verdict facade + W198 permit issuance (ALLOW/REVIEW/BLOCK + --persist).",
    inputs=["staged_diff", "diff_text", "symbol_name", "scope", "expires_at", "issued_to"],
    outputs=["verdict", "reason", "allowed_actions", "blocked_actions", "permit_id"],
    examples=[
        "git diff --cached | roam permit",
        "roam permit --symbol AuthService",
        "roam permit --input my-patch.diff",
        "roam permit issue --scope 'pr-42' --expires-at 2026-12-31T23:59:59Z --issued-to agent:foo --persist",
        "roam permit list",
    ],
    tags=["agent", "ci", "gate", "phase0"],
    ai_safe=True,
    requires_index=True,
    since="12.40",
    side_effect=True,
)
@click.group(
    name="permit",
    invoke_without_command=True,
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Read staged changes via `git diff --cached`. Default mode for pre-commit hooks.",
)
@click.option(
    "--input",
    "input_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read a unified diff from this file instead of git.",
)
@click.option(
    "--symbol",
    default=None,
    help="Run preflight against this symbol name in addition to (or instead of) a diff.",
)
@click.option(
    "--block-on-high-severity",
    type=int,
    default=1,
    help="Number of high-severity critique findings that triggers BLOCK (default 1).",
)
@click.option(
    "--review-on-blast-radius",
    type=int,
    default=60,
    help="Blast-radius threshold (symbols affected) that downgrades to REVIEW (default 60).",
)
@click.pass_context
def permit_cmd(
    ctx,
    staged: bool,
    input_file: str | None,
    symbol: str | None,
    block_on_high_severity: int,
    review_on_blast_radius: int,
):
    """Structural-permission verdict facade for AI agents + W198 issuance.

    Two surfaces sharing one command name:

    \b
    1. **Verdict facade** (default; no subcommand). Wraps `roam critique`
       and `roam preflight` and synthesises a single ALLOW / REVIEW /
       BLOCK decision over the staged change, a supplied diff, or a
       target symbol. NO permit_id is persisted on this path; it answers
       "is the change allowed RIGHT NOW given the analysis signals?"

    \b
    2. **W198 issuance** (`roam permit issue --persist`). Writes a real
       permit_id to .roam/permits/<id>.json so the W182
       AuthorityRef(authority_kind="permit") slot has a stable identity
       to bind to. Use `roam permit list` to enumerate, `roam permit
       show <id>` to inspect.

    \b
    Workflow examples (verdict-facade path):
      # pre-commit hook
      roam permit --staged || exit 1

      # Cursor rule
      roam permit --input changes.diff --json

      # Pre-edit safety check on a critical symbol
      roam permit --symbol open_db

      # Both diff + symbol context
      roam permit --staged --symbol open_db

    \b
    Workflow examples (W198 issuance path):
      # Issue a real permit (writes .roam/permits/<id>.json)
      roam permit issue --scope 'pr-42' \\
          --expires-at 2026-12-31T23:59:59Z \\
          --issued-to agent:claude --persist

      # Dry-run (synthesises an id but writes nothing)
      roam permit issue --scope 'pr-42' --expires-at ... --issued-to ...

      # Enumerate persisted permits
      roam permit list

    Exit codes (verdict-facade path): 0=ALLOW, 5=BLOCK, 6=REVIEW.
    """
    ctx.ensure_object(dict)
    # Stash the verdict-facade options on ctx so the default callback can
    # see them when no subcommand is invoked. Subcommands ignore them.
    ctx.obj.setdefault("_permit_facade_opts", {})
    ctx.obj["_permit_facade_opts"].update(
        {
            "staged": staged,
            "input_file": input_file,
            "symbol": symbol,
            "block_on_high_severity": block_on_high_severity,
            "review_on_blast_radius": review_on_blast_radius,
        }
    )
    if ctx.invoked_subcommand is not None:
        return
    # No subcommand -- run the verdict-facade engine.
    _run_verdict_facade(
        ctx,
        staged=staged,
        input_file=input_file,
        symbol=symbol,
        block_on_high_severity=block_on_high_severity,
        review_on_blast_radius=review_on_blast_radius,
    )


def _run_verdict_facade(
    ctx,
    *,
    staged: bool,
    input_file: str | None,
    symbol: str | None,
    block_on_high_severity: int,
    review_on_blast_radius: int,
) -> None:
    """Execute the verdict-facade path. Same semantics as the pre-W198 command."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    diff_text = ""
    if staged:
        diff_text = _acquire_staged_diff()
    elif input_file:
        diff_text = Path(input_file).read_text(encoding="utf-8", errors="replace")

    critique_result: dict = {"summary": {}}
    if diff_text.strip():
        critique_result = _capture_critique(diff_text)

    preflight_result: dict | None = None
    if symbol:
        preflight_result = _capture_preflight(symbol)

    verdict = _verdict_from_signals(
        critique_result,
        preflight_result,
        block_on_high_severity=block_on_high_severity,
        review_on_blast_radius=review_on_blast_radius,
    )

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "permit",
                    summary={
                        "verdict": verdict["verdict"],
                        "reason": verdict["reason"],
                        "allowed_actions": verdict["allowed_actions"],
                        "blocked_actions": verdict["blocked_actions"],
                        "diff_lines": diff_text.count("\n") if diff_text else 0,
                        "symbol": symbol,
                    },
                    critique=critique_result,
                    preflight=preflight_result,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict['verdict']}")
        click.echo(f"  reason:          {verdict['reason']}")
        if verdict["allowed_actions"]:
            click.echo(f"  allowed actions: {', '.join(verdict['allowed_actions'])}")
        if verdict["blocked_actions"]:
            click.echo(f"  blocked actions: {', '.join(verdict['blocked_actions'])}")
        if not diff_text and not symbol:
            click.echo()
            click.echo(
                "(no diff or symbol provided - pass --staged, --input PATH, or --symbol NAME)",
                err=True,
            )

    sys.exit(_exit_for_verdict(verdict["verdict"]))


# ---------------------------------------------------------------------------
# W198 — `roam permit issue` subcommand
# ---------------------------------------------------------------------------


def _validate_reason(reason: str) -> str | None:
    """Return an error string if *reason* violates the single-line rule.

    Multi-line bodies are rejected per the W247a body-prohibition
    discipline applied broadly. The CLI never accepts secrets / prompts /
    long-form rationale; operators reference external audit-trail rows
    instead.
    """
    if reason is None:
        return None
    if "\n" in reason or "\r" in reason:
        return (
            "--reason must be a single line (no newlines); multi-line bodies "
            "are rejected per the no-body / no-secrets discipline"
        )
    return None


@permit_cmd.command("issue")
@click.option(
    "--scope",
    required=True,
    help="Non-empty string describing what this permit authorises (e.g. 'pr-42', 'auth-changes', 'module:billing').",
)
@click.option(
    "--expires-at",
    required=True,
    help="ISO-8601 UTC timestamp after which the permit is treated as expired (e.g. 2026-12-31T23:59:59Z).",
)
@click.option(
    "--issued-to",
    required=True,
    help="Identity that holds this permit (e.g. 'agent:claude', 'human:alice@example.com'). Matches ActorRef.actor_id convention.",
)
@click.option(
    "--issued-by",
    default="",
    help="Operator that issued this permit. Defaults to 'human:operator' when omitted.",
)
@click.option(
    "--reason",
    default="",
    help="Optional single-line rationale (no body, no secrets).",
)
@click.option(
    "--id",
    "permit_id",
    default=None,
    help="Override the auto-generated permit_id (mainly for tests / deterministic CI).",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Write the permit to .roam/permits/<permit_id>.json (W198). Without --persist the command is a dry-run.",
)
@click.pass_context
def permit_issue(
    ctx,
    scope: str,
    expires_at: str,
    issued_to: str,
    issued_by: str,
    reason: str,
    permit_id: str | None,
    persist: bool,
):
    """Issue a permit. With --persist, writes ``.roam/permits/<id>.json``.

    \b
    W198: the writer side of the permit substrate. Without ``--persist``,
    behaves as a dry-run that synthesises an id and emits an envelope but
    writes nothing to disk -- backward compat with the pre-W198 verdict-
    facade-only world.

    \b
    On-disk shape (matches what cmd_pr_bundle._load_permits_from_disk reads):
      {
        "permit_id": "permit_20260515_a1b2c3",
        "scope": "...",
        "expires_at": "2026-06-15T00:00:00Z",
        "issued_to": "agent:foo",
        "issued_at": "2026-05-15T10:30:00Z",
        "issued_by": "human:operator",
        "reason": "optional single-line rationale"
      }

    When an active run is in flight, the issuance event auto-logs with
    ``permit_id`` stamped on the run-ledger event so the W292 collector
    harvester picks it up and promotes the matching AuthorityRef to
    ``provenance="run_ledger"`` (W294 corroboration channel).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Single-line discipline: refuse multi-line --reason values up front.
    err = _validate_reason(reason)
    if err is not None:
        envelope = json_envelope(
            "permit-issue",
            summary={
                "verdict": err,
                "state": "usage_error",
                "partial_success": True,
                "persisted": False,
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {err}")
        ctx.exit(EXIT_USAGE)

    issued_by_effective = issued_by.strip() or "human:operator"

    root = find_project_root()

    try:
        if persist:
            record, on_disk_path = issue_permit(
                root,
                PermitRequest(
                    scope=scope,
                    expires_at=expires_at,
                    issued_to=issued_to,
                    issued_by=issued_by_effective,
                    reason=reason,
                ),
                permit_id=permit_id,
            )
            on_disk_path_str: str | None = str(on_disk_path)
        else:
            # Dry-run: build the PermitRecord but DON'T write. Use the same
            # id-generation logic so the synthesised id is honest.
            from roam.permits.store import _make_permit_id, _utc_now_iso

            ts = _utc_now_iso()
            synthesised_id = permit_id or _make_permit_id(ts, issued_to, scope)
            record = PermitRecord(
                permit_id=synthesised_id,
                scope=scope,
                expires_at=expires_at,
                issued_to=issued_to,
                issued_at=ts,
                issued_by=issued_by_effective,
                reason=reason,
            )
            on_disk_path_str = None
    except ValueError as exc:
        verdict = f"invalid permit: {exc}"
        envelope = json_envelope(
            "permit-issue",
            summary={
                "verdict": verdict,
                "state": "usage_error",
                "partial_success": True,
                "persisted": False,
            },
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    if persist:
        verdict = (
            f"issued permit {record.permit_id} "
            f"(scope={record.scope}, issued_to={record.issued_to}, "
            f"expires {record.expires_at})"
        )
        state = "persisted"
    else:
        verdict = (
            f"dry-run permit {record.permit_id} "
            f"(scope={record.scope}, issued_to={record.issued_to}, "
            f"expires {record.expires_at}; pass --persist to write)"
        )
        state = "dry_run"

    envelope = json_envelope(
        "permit-issue",
        summary={
            "verdict": verdict,
            "state": state,
            "partial_success": False,
            "persisted": persist,
            "permit_id": record.permit_id,
            "expires_at": record.expires_at,
        },
        permit=record.to_dict(),
        path=on_disk_path_str,
        agent_contract={
            "facts": [
                f"permit_id: {record.permit_id}",
                f"scope: {record.scope}",
                f"issued_to: {record.issued_to}",
                f"expires_at: {record.expires_at}",
            ],
            "next_commands": [
                "roam permit list",
                f"roam permit show {record.permit_id}",
            ],
        },
    )

    # W294 corroboration: stamp ``permit_id`` on the auto-logged event
    # when --persist actually wrote a real permit AND an active run
    # exists. The W292 collector harvester reads this field to promote
    # the matching AuthorityRef from ``producer_envelope(permit)`` to
    # ``provenance="run_ledger"``. The whitelist filter in auto_log
    # short-circuits silently when no active run is present.
    if persist:
        # auto_log is documented + verified to never raise.
        auto_log(
            envelope,
            action="permit-issue",
            target=record.permit_id,
            repo_root=root,
            extra_event_fields={"permit_id": record.permit_id},
        )

    if json_mode:
        click.echo(to_json(envelope))
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  permit_id:  {record.permit_id}")
    click.echo(f"  scope:      {record.scope}")
    click.echo(f"  issued_to:  {record.issued_to}")
    click.echo(f"  issued_by:  {record.issued_by}")
    click.echo(f"  issued_at:  {record.issued_at}")
    click.echo(f"  expires_at: {record.expires_at}")
    if record.reason:
        click.echo(f"  reason:     {record.reason}")
    if on_disk_path_str:
        click.echo(f"  path:       {on_disk_path_str}")
    else:
        click.echo("  (dry-run -- pass --persist to write to disk)")


# ---------------------------------------------------------------------------
# `roam permit list` -- enumerate persisted permits
# ---------------------------------------------------------------------------


def _permit_table_row(rec: PermitRecord) -> list[str]:
    return [
        rec.permit_id,
        rec.scope,
        rec.issued_to,
        rec.issued_at,
        rec.expires_at,
    ]


@permit_cmd.command("list")
@click.pass_context
def permit_list(ctx):
    """List persisted permits in this repo, newest first.

    Empty state (no permits yet) returns a clean envelope with
    ``state: no_permits`` -- never an error or empty stdout (Pattern 1).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    proot = permits_root(root)

    if not proot.exists():
        verdict = "no permits yet -- run `roam permit issue --persist ...` to open one"
        envelope = json_envelope(
            "permit-list",
            summary={
                "verdict": verdict,
                "state": "no_permits",
                "partial_success": False,
                "total": 0,
            },
            permits=[],
            path=str(proot),
        )
        if json_mode:
            click.echo(to_json(envelope))
            return
        click.echo(f"VERDICT: {verdict}")
        return

    permits = list_permits(root)
    total = len(permits)
    verdict = f"{total} permit{'s' if total != 1 else ''}" if total else "no permits in this repo"
    envelope = json_envelope(
        "permit-list",
        summary={
            "verdict": verdict,
            "state": "ok" if total else "no_permits",
            "partial_success": False,
            "total": total,
        },
        permits=[p.to_dict() for p in permits],
        path=str(proot),
    )
    if json_mode:
        click.echo(to_json(envelope))
        return
    click.echo(f"VERDICT: {verdict}")
    if total == 0:
        return
    rows = [_permit_table_row(p) for p in permits]
    click.echo(format_table(["Permit", "Scope", "Issued To", "Issued At", "Expires"], rows))


# ---------------------------------------------------------------------------
# `roam permit show` -- dump one persisted permit
# ---------------------------------------------------------------------------


@permit_cmd.command("show")
@click.argument("permit_id")
@click.pass_context
def permit_show(ctx, permit_id):
    """Dump a single persisted permit record."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    rec = read_permit(root, permit_id)
    if rec is None:
        verdict = f"permit {permit_id} does not exist -- run `roam permit list` to find a valid permit_id"
        envelope = json_envelope(
            "permit-show",
            summary={
                "verdict": verdict,
                "state": "unknown_permit",
                "partial_success": True,
                "total": 0,
            },
            permit=None,
        )
        if json_mode:
            click.echo(to_json(envelope))
            ctx.exit(EXIT_USAGE)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(EXIT_USAGE)

    verdict = f"permit {rec.permit_id} scope={rec.scope} issued_to={rec.issued_to} expires {rec.expires_at}"
    envelope = json_envelope(
        "permit-show",
        summary={
            "verdict": verdict,
            "state": "ok",
            "partial_success": False,
            "total": 1,
            "permit_id": rec.permit_id,
        },
        permit=rec.to_dict(),
    )
    if json_mode:
        click.echo(to_json(envelope))
        return
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  permit_id:  {rec.permit_id}")
    click.echo(f"  scope:      {rec.scope}")
    click.echo(f"  issued_to:  {rec.issued_to}")
    click.echo(f"  issued_by:  {rec.issued_by}")
    click.echo(f"  issued_at:  {rec.issued_at}")
    click.echo(f"  expires_at: {rec.expires_at}")
    if rec.reason:
        click.echo(f"  reason:     {rec.reason}")
