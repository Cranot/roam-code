"""`roam guard-init` — bootstrap a repo for Roam Guard adoption.

SARIF is deliberately NOT emitted: this command creates directories
+ prints next-step adoption hints — it has no code findings to report
and no source locations to populate.

Creates the minimum directory + file layout an adopter needs to start
using `roam guard-pr`:

  * `.roam/` directory
  * `.roam/pr-bundles/` directory
  * Optional `.roam-guard-rules.yml` stub (when --with-rules-stub)

Idempotent — re-running on an initialized repo is a no-op + reports
"already initialized". Prints next-step commands.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.guard_errors import guard_error_envelope
from roam.guard_log import log_path_for
from roam.output.formatter import json_envelope, to_json

_RULES_STUB = """# Roam Guard rule pack — repo-local overrides.
#
# `extends: default` inherits the built-in pack. Add or override rules
# below to customize verification for this repo. Run
# `roam guard-rules show --rules .roam-guard-rules.yml` to inspect the
# merged pack. Run `roam guard-rules validate .roam-guard-rules.yml`
# before committing changes here.

extends: default

# Example: require an extra check when touching billing code.
# rules:
#   - id: billing_requires_integration_tests
#     paths: ["src/billing/**"]
#     required_checks:
#       - command: "pytest tests/integration/test_billing.py"
#         kind: "test"
#         reason: "billing change must pass integration tests"
"""


@click.command(name="guard-init")
@click.option(
    "--with-rules-stub",
    "with_rules_stub",
    is_flag=True,
    default=False,
    help="Also write a `.roam-guard-rules.yml` stub at the repo root.",
)
@click.option(
    "--force", "force", is_flag=True, default=False, help="Overwrite an existing `.roam-guard-rules.yml` stub."
)
@click.pass_context
@roam_capability(
    name="guard-init",
    category="planning",
    summary="Bootstrap a repo for Roam Guard adoption",
    inputs=(),
    outputs=("guard_init_report",),
    examples=(
        "roam guard-init",
        "roam guard-init --with-rules-stub",
    ),
    tags=("planning", "roam-guard", "init"),
    side_effect=True,
)
def guard_init(ctx: click.Context, with_rules_stub: bool, force: bool) -> None:
    """Bootstrap `.roam/` + optional rule-pack stub for Roam Guard."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = Path(find_project_root() or Path.cwd())

    created: list[str] = []
    existing: list[str] = []

    dot_roam = root / ".roam"
    bundles = dot_roam / "pr-bundles"

    for path in (dot_roam, bundles):
        if path.is_dir():
            existing.append(str(path.relative_to(root)))
        else:
            try:
                path.mkdir(parents=True, exist_ok=True)
                created.append(str(path.relative_to(root)))
            except OSError as e:
                msg = f"Could not create {path}: {e}"
                if json_mode:
                    click.echo(
                        to_json(
                            guard_error_envelope(
                                "guard-init",
                                "io_error",
                                msg,
                                context={"path": str(path)},
                            )
                        )
                    )
                else:
                    click.echo(msg, err=True)
                ctx.exit(2)
                return

    rules_path = root / ".roam-guard-rules.yml"
    rules_written: str | None = None
    if with_rules_stub:
        if rules_path.is_file() and not force:
            existing.append(str(rules_path.relative_to(root)))
        else:
            rules_path.write_text(_RULES_STUB, encoding="utf-8")
            created.append(str(rules_path.relative_to(root)))
            rules_written = str(rules_path.relative_to(root))

    log = log_path_for(root)
    log_exists = log.is_file()

    next_commands = [
        "roam pr-bundle init",
        "roam guard-pr --dry-run",
        "roam guard-doctor",
    ]

    summary = {
        "verdict": (
            f"initialized {len(created)} paths, {len(existing)} already present" if created else "already initialized"
        ),
        "created_count": len(created),
        "existing_count": len(existing),
        "log_exists": log_exists,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "guard-init",
                    summary=summary,
                    agent_contract={
                        "facts": [
                            f"{len(created)} paths created",
                            f"{len(existing)} paths existed",
                            f"rules stub written: {rules_written}" if rules_written else "no rules stub written",
                        ],
                        "next_commands": next_commands,
                        "risks": [],
                    },
                    created=created,
                    existing=existing,
                    root=str(root),
                    rules_stub=rules_written,
                )
            )
        )
        return

    if created:
        click.echo(f"Created ({len(created)}):")
        for p in created:
            click.echo(f"  + {p}")
    if existing:
        click.echo(f"Already present ({len(existing)}):")
        for p in existing:
            click.echo(f"  · {p}")
    click.echo("")
    click.echo("Next steps:")
    for cmd in next_commands:
        click.echo(f"  $ {cmd}")
