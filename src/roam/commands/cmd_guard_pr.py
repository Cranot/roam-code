"""`roam guard-pr` — the Roam Guard MVP aggregate command.

SARIF is deliberately NOT wired to the global --sarif flag: structured
SARIF output ships via `--format sarif` on `roam proof-bundle`, which is
the canonical SARIF emitter for the verdict surface. guard-pr stays
focused on the aggregate run + CI exit code.

One CLI invocation that runs the full Phase 1+2 flow:

  1. Find pr-bundle on current branch (or use --bundle)
  2. Auto-collect — fold response envelopes from .roam/responses/ into bundle
  3. Save bundle back to disk
  4. Compose AgentChangeProofBundle v1
  5. Render in requested format (text / markdown / json)
  6. Optionally POST to GitHub Check Run API
  7. Exit per verdict (0 = pass/warnings, 4 = needs_review, 5 = blocked under --strict)

Distinct from `roam guard` (per-symbol pre-edit packet).

Per the Roam Guard pivot decision, this is the
demoable Roam Guard CLI sigil.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.github_check import build_check_run_payload, post_check_run
from roam.guard_errors import guard_error_envelope
from roam.guard_log import append_log_entry, build_log_entry
from roam.guard_rules import get_active_rules
from roam.output.formatter import json_envelope, to_json
from roam.pr_bundle_primitives import (
    atomic_write_bundle,
    auto_collect,
    discover_active_bundle,
    empty_bundle,
    load_bundle,
)
from roam.pr_bundle_primitives import (
    bundle_path as canonical_bundle_path,
)
from roam.proof_bundle import (
    PROOF_BUNDLE_SCHEMA,
    compose_agent_change_proof_bundle,
    load_pr_bundle,
    render_markdown,
)
from roam.verdict import verdict_exit_code


def _find_bundle_path(bundle_arg: str | None) -> Path | None:
    """Thin wrapper that delegates to the canonical pr_bundle_primitives helper."""
    root = find_project_root()
    return discover_active_bundle(
        Path(root) if root else None,
        bundle_arg,
    )


def _init_bundle_if_missing(bundle_arg: str | None, intent: str) -> Path | None:
    """CI-friendly initializer. Creates `.roam/pr-bundles/<branch>.json` if it
    doesn't exist yet. Returns the path or None on failure.

    Uses the stable `pr_bundle_primitives` boundary so we don't diverge from
    the canonical bundle shape.
    """
    if bundle_arg:
        target = Path(bundle_arg)
    else:
        root = find_project_root()
        if root is None:
            return None
        target = canonical_bundle_path(Path(root))

    if target.is_file():
        return target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bundle(target, empty_bundle(intent))
        return target
    except OSError:
        return None


def _run_auto_collect_inline(bundle_path: Path, root: Path) -> dict:
    """Best-effort auto-collect — folds responses into bundle, saves on disk.

    Uses the stable pr_bundle_primitives boundary. Failure is non-fatal;
    we proceed with whatever the bundle has.
    """
    bundle = load_bundle(bundle_path)
    if bundle is None:
        return {"error": "bundle_load_failed"}
    try:
        totals = auto_collect(bundle, root)
        atomic_write_bundle(bundle_path, bundle)
        return totals
    except Exception as e:  # pragma: no cover - protective
        return {"error": f"auto_collect_failed: {e}"}


@click.command(name="guard-pr")
@click.option(
    "--bundle", "-b", type=str, default=None, help="Path to pr-bundle JSON. Default: auto-discover for current branch."
)
@click.option(
    "--mode",
    type=click.Choice(["read_only", "safe_edit", "migration", "autonomous_pr"]),
    default=None,
    help="Override mode (else use bundle's or safe_edit).",
)
@click.option(
    "--policy-profile", type=click.Choice(["startup", "regulated"]), default="startup", help="Policy profile floor."
)
@click.option("--strict", is_flag=True, default=False, help="Exit non-zero on blocked / needs_review.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "markdown", "json"]),
    default=None,
    help="Output format. JSON default with --json; otherwise text.",
)
@click.option("--output", "-o", type=str, default=None, help="Write the rendered output to this file.")
@click.option(
    "--skip-collect", is_flag=True, default=False, help="Skip auto-collect (use existing bundle state as-is)."
)
@click.option(
    "--init-if-missing",
    "init_if_missing",
    is_flag=True,
    default=False,
    help="Create an empty pr-bundle on disk if none exists. Useful for CI.",
)
@click.option(
    "--init-intent",
    type=str,
    default="auto-init via roam guard-pr",
    help="Intent string to use when --init-if-missing creates the bundle.",
)
@click.option(
    "--ci",
    "ci_preset",
    is_flag=True,
    default=False,
    help="CI preset: equivalent to --strict --init-if-missing --format markdown. Explicit flags override the preset.",
)
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a custom rule pack (YAML). Defaults to the built-in RulePack.default().",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show what WOULD be checked + the predicted verdict, "
    "without writing the bundle to disk, running auto-collect, "
    "appending to the verdict log, or posting a GitHub Check. "
    "Useful for CI debugging + 'will my PR pass?' local checks.",
)
@click.option(
    "--post-check",
    is_flag=True,
    default=False,
    help="POST the verdict to GitHub Check Runs API. Needs --gh-repo + --gh-sha + GITHUB_TOKEN.",
)
@click.option("--gh-repo", type=str, default=None, help="owner/repo for --post-check.")
@click.option("--gh-sha", type=str, default=None, help="Head SHA for the PR (40-char). Required by --post-check.")
@click.option("--gh-name", type=str, default="Roam Guard", help="Display name of the GitHub check.")
@click.option(
    "--details-url", type=str, default=None, help="Optional details_url for the GitHub check (e.g. dashboard link)."
)
@click.pass_context
@roam_capability(
    name="guard-pr",
    category="planning",
    summary="Aggregate Roam Guard pipeline: auto-collect → v1 bundle → verdict → render → optional GH Check",
    inputs=("pr_bundle",),
    outputs=("verdict", "agent_change_proof_bundle"),
    side_effect=True,  # appends verdict-log.jsonl + optional GH Check POST
    examples=(
        "roam guard-pr --strict",
        "roam guard-pr --format markdown --output verdict.md",
        "GITHUB_TOKEN=xxx roam guard-pr --post-check --gh-repo Cranot/roam-code --gh-sha $(git rev-parse HEAD)",
    ),
    tags=("planning", "proof-bundle", "verdict", "roam-guard", "ci"),
)
def guard_pr(
    ctx: click.Context,
    bundle: str | None,
    mode: str | None,
    policy_profile: str,
    strict: bool,
    fmt: str | None,
    output: str | None,
    skip_collect: bool,
    init_if_missing: bool,
    init_intent: str,
    ci_preset: bool,
    rules_path: str | None,
    dry_run: bool,
    post_check: bool,
    gh_repo: str | None,
    gh_sha: str | None,
    gh_name: str,
    details_url: str | None,
) -> None:
    """Run the full Roam Guard pipeline in one call."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # CI preset — explicit flags win (LAW 11).
    if ci_preset:
        if not strict:
            strict = True
        if not init_if_missing:
            init_if_missing = True
        if fmt is None and not json_mode:
            fmt = "markdown"

    bundle_path = _find_bundle_path(bundle)
    if bundle_path is None and init_if_missing:
        bundle_path = _init_bundle_if_missing(bundle, init_intent)
    if bundle_path is None:
        msg = "No pr-bundle found on disk."
        fix = "Run `roam pr-bundle init --intent ...` first, OR pass --bundle <path>, OR re-run with --init-if-missing."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "guard-pr",
                        "no_bundle_found",
                        msg,
                        fix=fix,
                        context={"bundle_arg": bundle},
                    )
                )
            )
        else:
            click.echo(f"{msg} {fix}", err=True)
        ctx.exit(2)
        return

    root = Path(find_project_root() or Path.cwd())

    collect_summary: dict | None = None
    # --dry-run implies --skip-collect (don't mutate the bundle on disk).
    if not skip_collect and not dry_run:
        collect_summary = _run_auto_collect_inline(bundle_path, root)

    try:
        bundle_dict = load_pr_bundle(bundle_path)
    except (ValueError, json.JSONDecodeError) as e:
        msg = f"Failed to parse bundle at {bundle_path}"
        fix = f"Inspect / repair the JSON at {bundle_path}, or delete it and re-run with --init-if-missing."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "guard-pr",
                        "bundle_parse_error",
                        msg,
                        fix=fix,
                        context={"bundle_path": str(bundle_path), "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"{msg}: {e}", err=True)
        ctx.exit(2)
        return

    try:
        active_rules = get_active_rules(rules_path)
    except ValueError as e:
        msg = f"Rule pack at {rules_path} is invalid"
        fix = "Run `roam guard-rules validate <path>` for details, or omit --rules to use the built-in default."
        if json_mode:
            click.echo(
                to_json(
                    guard_error_envelope(
                        "guard-pr",
                        "rule_pack_invalid",
                        msg,
                        fix=fix,
                        context={"rules_path": rules_path, "exception": str(e)},
                    )
                )
            )
        else:
            click.echo(f"{msg}: {e}", err=True)
        ctx.exit(2)
        return

    v1 = compose_agent_change_proof_bundle(
        bundle_dict,
        repo_root=root,
        mode=mode,
        policy_profile=policy_profile,
        rule_pack=active_rules,
    )

    verdict_value = (v1.get("verdict") or {}).get("value", "pass")
    exit_code = verdict_exit_code(verdict_value) if strict else 0

    # ---- persistent verdict log (.roam/verdict-log.jsonl) ----
    # Append-only; best-effort. Powers `roam guard-history` fast-path AND
    # gives an audit trail surviving bundle file rotation.
    # --dry-run skips this to keep the run side-effect free.
    if not dry_run:
        log_entry = build_log_entry(v1=v1, bundle_path=bundle_path)
        append_log_entry(root, log_entry)

    # ---- render ----
    markdown_body = render_markdown(v1)
    if fmt == "markdown":
        rendered = markdown_body
    elif fmt == "json":
        rendered = to_json(v1)
    elif fmt is None and json_mode:
        rendered = None  # JSON envelope wraps it below
    else:
        rendered = _text_render(v1, bundle_path)

    if output and not dry_run:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "markdown":
            out_path.write_text(markdown_body)
        elif fmt == "json":
            out_path.write_text(to_json(v1))
        else:
            out_path.write_text(rendered or markdown_body)

    # ---- optional GitHub Check Run POST ----
    # --dry-run skips the network call (no side effects).
    check_result: dict | None = None
    if post_check and not dry_run:
        if not gh_repo or not gh_sha:
            check_result = {"ok": False, "error": "missing_gh_repo_or_sha"}
        elif "/" not in gh_repo:
            check_result = {"ok": False, "error": "gh_repo_must_be_owner_slash_repo"}
        else:
            owner, repo_name = gh_repo.split("/", 1)
            payload = build_check_run_payload(
                v1,
                head_sha=gh_sha,
                name=gh_name,
                markdown=markdown_body,
                details_url=details_url,
            )
            check_result = post_check_run(owner=owner, repo=repo_name, payload=payload)

    # ---- emit ----
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-pr",
                    summary={
                        "verdict": verdict_value,
                        "schema": PROOF_BUNDLE_SCHEMA,
                        "exit_code": exit_code,
                        "required_count": len(v1["verification_contract"]["required"]),
                        "executed_count": len(v1["executed_checks"]),
                        "missing_count": len(v1["missing_checks"]),
                        "changed_files_count": len(v1["changed_files"]),
                        "dry_run": dry_run,
                        "partial_success": verdict_value in ("blocked", "needs_review"),
                    },
                    agent_contract={
                        "facts": [
                            f"verdict {verdict_value}",
                            f"{len(v1['changed_files'])} files changed",
                            f"{len(v1['verification_contract']['required'])} checks required",
                            f"{len(v1['executed_checks'])} checks executed",
                        ],
                        "next_commands": ["roam pr-bundle add affected <symbol>"]
                        if verdict_value == "blocked"
                        else ["roam pr-bundle emit"],
                        "risks": [
                            r
                            for r in v1["verdict"]["reasons"]
                            if r.get("code")
                            in {
                                "required_check_failed",
                                "required_check_not_run",
                                "high_risk_path",
                            }
                        ],
                    },
                    agent_change_proof_bundle=v1,
                    auto_collect=collect_summary,
                    github_check_result=check_result,
                )
            )
        )
    else:
        if dry_run:
            click.echo("[dry-run] no bundle mutation, no log append, no GH post.\n")
        click.echo(rendered or markdown_body)
        if check_result is not None:
            if check_result.get("ok"):
                click.echo(f"\n[github-check]: posted (status {check_result.get('status', '?')})")
            else:
                # L4 fix (W33e): surface the full failure context so CI logs
                # don't silently swallow rate limits / auth errors.
                err = check_result.get("error", "unknown")
                status = check_result.get("status", "?")
                body = check_result.get("body")
                click.echo(f"\n[github-check]: FAILED — error={err} status={status}")
                if body:
                    body_str = str(body)[:500]
                    click.echo(f"  body: {body_str}")

    ctx.exit(exit_code)


def _text_render(v1: dict, bundle_path: Path) -> str:
    """Compact text output (default when no --format / no --json)."""
    verdict_value = (v1.get("verdict") or {}).get("value", "pass")
    lines = [
        f"VERDICT: {verdict_value}",
        f"  bundle: {bundle_path}",
        f"  changed_files: {len(v1['changed_files'])}",
        f"  required: {len(v1['verification_contract']['required'])}",
        f"  executed: {len(v1['executed_checks'])}",
        f"  missing:  {len(v1['missing_checks'])}",
        f"  risk:     {v1['risk'].get('level', 'low')}",
    ]
    for r in (v1.get("verdict", {}) or {}).get("reasons", [])[:5]:
        lines.append(f"  reason: {r.get('code')}")
    return "\n".join(lines)
