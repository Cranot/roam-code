"""`roam guard-rules` — inspect / validate / test verification rule packs.

SARIF is deliberately NOT emitted: output is rule-pack introspection
(show/validate/test subcommands) — there are no per-file code findings
to surface in SARIF format.

Three subcommands:
  * `roam guard-rules show`              — dump the active rule pack as YAML
  * `roam guard-rules validate <path>`   — parse-check a YAML pack file
  * `roam guard-rules test <file_path>`  — show which rules match a path

Powers adoption: lets users see exactly what rules are active, validate
their custom YAML before pushing it, and dry-run rule matching against
representative file paths.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.guard_rules import get_active_rules, load_rule_pack
from roam.output.formatter import json_envelope, to_json


@click.group(name="guard-rules")
@click.pass_context
@roam_capability(
    name="guard-rules",
    category="planning",
    summary="Inspect / validate / test Roam Guard rule packs",
    inputs=(),
    outputs=("rule_pack_inspection",),
    examples=(
        "roam guard-rules show",
        "roam guard-rules validate my-pack.yml",
        "roam guard-rules test src/auth/x.py",
    ),
    tags=("planning", "roam-guard", "rules"),
)
def guard_rules_group(ctx: click.Context) -> None:
    """Inspect / validate / test Roam Guard verification rule packs."""
    pass


# ---- subcommand: show ----


@guard_rules_group.command("show")
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Show this rule pack (else show the default).",
)
@click.pass_context
@roam_capability(
    name="guard-rules show",
    category="planning",
    summary="Dump the active rule pack as YAML",
    inputs=("rule_pack",),
    outputs=("pack_yaml",),
    examples=("roam guard-rules show", "roam guard-rules show --rules my.yml"),
    tags=("planning", "roam-guard", "rule-pack"),
)
def show(ctx: click.Context, rules_path: str | None) -> None:
    """Print the active rule pack as YAML (the canonical export format)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    try:
        pack = get_active_rules(rules_path)
    except ValueError as e:
        msg = f"Failed to load rule pack: {e}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-rules-show",
                        summary={"verdict": "load_error", "partial_success": True, "error": str(e)},
                        agent_contract={"facts": [msg], "next_commands": [], "risks": []},
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-rules-show",
                    summary={
                        "verdict": f"pack `{pack.name}` with {len(pack.file_patterns)} pattern(s)",
                        "name": pack.name,
                        "version": pack.version,
                        "pattern_count": len(pack.file_patterns),
                        "partial_success": False,
                    },
                    agent_contract={
                        "facts": [f"pack {pack.name}", f"{len(pack.file_patterns)} patterns"],
                        "next_commands": ["roam guard-rules test <file>"],
                        "risks": [],
                    },
                    pack=pack.to_dict(),
                )
            )
        )
        return

    # Text: emit raw YAML for copy-paste. yaml is a hard dep of guard_rules
    # itself (rule-pack loading), so this import is guaranteed-available
    # whenever a pack loaded successfully above.
    import yaml  # type: ignore  # unguarded-import: ok

    click.echo(yaml.safe_dump(pack.to_dict(), sort_keys=False, default_flow_style=False))


# ---- subcommand: validate ----


@guard_rules_group.command("validate")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
@roam_capability(
    name="guard-rules validate",
    category="planning",
    summary="Parse-check a YAML rule pack file — exit 0 valid, 2 invalid",
    inputs=("rule_pack_path",),
    outputs=("validation_verdict",),
    examples=("roam guard-rules validate .roam/guard-rules.yml",),
    tags=("planning", "roam-guard", "rule-pack", "validate"),
)
def validate(ctx: click.Context, path: str) -> None:
    """Parse and structurally validate a YAML rule pack."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    try:
        pack = load_rule_pack(Path(path))
    except ValueError as e:
        msg = f"INVALID: {e}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-rules-validate",
                        summary={"verdict": "invalid", "partial_success": True, "error": str(e)},
                        agent_contract={
                            "facts": [msg],
                            "next_commands": ["roam guard-rules show"],
                            "risks": [{"code": "rule_pack_invalid", "detail": str(e)}],
                        },
                        path=path,
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    n = len(pack.file_patterns)
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-rules-validate",
                    summary={
                        "verdict": "valid",
                        "name": pack.name,
                        "version": pack.version,
                        "pattern_count": n,
                        "partial_success": False,
                    },
                    agent_contract={
                        "facts": [f"VALID: pack `{pack.name}` v{pack.version}", f"{n} patterns"],
                        "next_commands": ["roam guard-pr --rules " + path],
                        "risks": [],
                    },
                    path=path,
                )
            )
        )
    else:
        click.echo(f"VALID: pack `{pack.name}` v{pack.version} with {n} pattern(s)")
        for rule in pack.file_patterns[:10]:
            kinds = ",".join(sorted(rule.applies_to_kinds))
            click.echo(f"  - {rule.id} → kinds=[{kinds}]")
        if n > 10:
            click.echo(f"  ... and {n - 10} more")


# ---- subcommand: test ----


def _match_rules(pack, file_path: str) -> list[dict[str, object]]:
    """Adapt `RulePack.matches_path` to the dict shape this command emits."""
    return [
        {
            "id": rule.id,
            "regex": rule.regex.pattern,
            "applies_to_kinds": sorted(rule.applies_to_kinds),
        }
        for rule in pack.matches_path(file_path)
    ]


@guard_rules_group.command("test")
@click.argument("path", type=str, required=False)
@click.option(
    "--rules",
    "rules_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Test against this rule pack (else the default).",
)
@click.option(
    "--from-bundle",
    "from_bundle",
    is_flag=True,
    default=False,
    help="Test against every changed_file in the active pr-bundle.",
)
@click.option("--bundle", "bundle_path", type=str, default=None, help="Explicit bundle path (only with --from-bundle).")
@click.pass_context
@roam_capability(
    name="guard-rules test",
    category="planning",
    summary="Show which rules match a given file path",
    inputs=("path", "rule_pack"),
    outputs=("matching_rules",),
    examples=(
        "roam guard-rules test src/auth/session.py",
        "roam guard-rules test --from-bundle",
        "roam --json guard-rules test app/Http/Controllers/AuthController.php",
    ),
    tags=("planning", "roam-guard", "rule-pack", "test"),
)
def test(
    ctx: click.Context, path: str | None, rules_path: str | None, from_bundle: bool, bundle_path: str | None
) -> None:
    """Dry-run rule matching against a file path OR every file in the active bundle."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    try:
        pack = get_active_rules(rules_path)
    except ValueError as e:
        msg = f"Failed to load rule pack: {e}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-rules-test",
                        summary={"verdict": "load_error", "partial_success": True, "error": str(e)},
                        agent_contract={"facts": [msg], "next_commands": [], "risks": []},
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    if from_bundle:
        from pathlib import Path as _P

        from roam.db.connection import find_project_root
        from roam.pr_bundle_primitives import discover_active_bundle
        from roam.proof_bundle import compose_agent_change_proof_bundle, load_pr_bundle

        root = _P(find_project_root() or _P.cwd())
        bundle_p = discover_active_bundle(root, bundle_path)
        if bundle_p is None:
            msg = "No active pr-bundle found"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "guard-rules-test",
                            summary={"verdict": "no_bundle", "partial_success": True, "error": msg},
                            agent_contract={
                                "facts": [msg],
                                "next_commands": ["roam pr-bundle init", "roam guard-init"],
                                "risks": [],
                            },
                        )
                    )
                )
            else:
                click.echo(msg, err=True)
            ctx.exit(2)
            return

        bundle = load_pr_bundle(bundle_p)
        v1 = compose_agent_change_proof_bundle(bundle, repo_root=root)
        files = v1.get("changed_files") or []
        per_file = []
        total_matches = 0
        for f in files:
            m = _match_rules(pack, f)
            per_file.append({"file": f, "matches": m, "match_count": len(m)})
            total_matches += len(m)

        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-rules-test",
                        summary={
                            "verdict": f"{total_matches} match(es) across {len(files)} file(s)",
                            "file_count": len(files),
                            "total_matches": total_matches,
                            "pack": pack.name,
                            "bundle": str(bundle_p),
                            "partial_success": False,
                        },
                        agent_contract={
                            "facts": [
                                f"{len(files)} files tested",
                                f"{total_matches} rule matches",
                                f"pack {pack.name}",
                            ],
                            "next_commands": ["roam guard-rules show", "roam guard-pr --dry-run"],
                            "risks": [],
                        },
                        per_file=per_file,
                    )
                )
            )
        else:
            click.echo(f"PACK: {pack.name} v{pack.version} — bundle {bundle_p}")
            click.echo(f"{len(files)} file(s), {total_matches} match(es) total")
            for entry in per_file[:30]:
                if entry["match_count"]:
                    click.echo(f"  {entry['file']} → {entry['match_count']} match(es)")
                    for m in entry["matches"]:
                        kinds = ",".join(m["applies_to_kinds"])  # type: ignore[arg-type]
                        click.echo(f"    - {m['id']} → kinds=[{kinds}]")
            if len(per_file) > 30:
                click.echo(f"  ... and {len(per_file) - 30} more files")
        return

    if not path:
        msg = "Pass either a PATH argument or --from-bundle."
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "guard-rules-test",
                        summary={"verdict": "missing_input", "partial_success": True, "error": msg},
                        agent_contract={
                            "facts": [msg],
                            "next_commands": [
                                "roam guard-rules test src/auth/x.py",
                                "roam guard-rules test --from-bundle",
                            ],
                            "risks": [],
                        },
                    )
                )
            )
        else:
            click.echo(msg, err=True)
        ctx.exit(2)
        return

    matches = _match_rules(pack, path)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-rules-test",
                    summary={
                        "verdict": f"{len(matches)} rule(s) match",
                        "file": path,
                        "match_count": len(matches),
                        "pack": pack.name,
                        "partial_success": False,
                    },
                    agent_contract={
                        "facts": [
                            f"file {path}",
                            f"{len(matches)} rules match",
                            f"pack {pack.name}",
                        ],
                        "next_commands": ["roam guard-rules show"],
                        "risks": [],
                    },
                    matches=matches,
                )
            )
        )
    else:
        if not matches:
            click.echo(f"NO MATCH: {path} (pack `{pack.name}`, {len(pack.file_patterns)} rules tried)")
        else:
            click.echo(f"MATCHES ({len(matches)}): {path}")
            for m in matches:
                kinds = ",".join(m["applies_to_kinds"])  # type: ignore[arg-type]
                click.echo(f"  - {m['id']} → kinds=[{kinds}]")
