"""Initialize a project for Roam: index, config, CI workflow."""

import click

from roam.db.connection import db_exists, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


_FITNESS_YAML = """\
rules:
  - name: No circular imports in core
    type: dependency
    source: "src/**"
    forbidden_target: "tests/**"
    reason: "Production code should not import test modules"
  - name: Complexity threshold
    type: metric
    metric: cognitive_complexity
    threshold: 30
    reason: "Functions above 30 cognitive complexity need refactoring"
"""

_GITHUB_WORKFLOW = """\
name: Roam Code Analysis
on:
  pull_request:
    branches: [main, master]
permissions:
  contents: read
  pull-requests: write
jobs:
  roam:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install roam-code
      - run: roam index
      - run: roam fitness
      - run: roam pr-risk --json
"""

_WELCOME = """\
Roam initialized! Getting-started path:

  1. roam understand        -- Full codebase overview
  2. roam health            -- Health score and issues
  3. roam preflight <file>  -- Safety-check before changes
  4. roam pr-risk           -- Score pending changes
  5. roam fitness           -- Enforce architectural rules

Created:
{created_lines}
Run `roam --help` for all commands."""


@click.command("init")
@click.option("--root", default=".", help="Project root")
@click.option("--yes", is_flag=True, help="Non-interactive, accept defaults")
@click.pass_context
def init(ctx, root, yes):
    """Initialize Roam for this project: index, config, CI workflow."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    project_root = find_project_root(root)

    created = []
    skipped = []

    # 1. Create .roam/ directory
    roam_dir = project_root / ".roam"
    roam_dir.mkdir(exist_ok=True)

    # 2. Run indexing if no index exists
    had_index = db_exists(project_root)
    if not had_index:
        if not json_mode:
            click.echo("No index found. Building...")
    ensure_index()

    # 3. Generate .roam/fitness.yaml
    fitness_path = roam_dir / "fitness.yaml"
    if not fitness_path.exists():
        fitness_path.write_text(_FITNESS_YAML, encoding="utf-8")
        created.append(".roam/fitness.yaml")
    else:
        skipped.append(".roam/fitness.yaml")

    # 4. Generate .github/workflows/roam.yml
    workflow_dir = project_root / ".github" / "workflows"
    workflow_path = workflow_dir / "roam.yml"
    if not workflow_path.exists():
        workflow_dir.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(_GITHUB_WORKFLOW, encoding="utf-8")
        created.append(".github/workflows/roam.yml")
    else:
        skipped.append(".github/workflows/roam.yml")

    # 5. Quick health summary
    health_summary = {}
    try:
        from roam.db.connection import open_db
        from roam.commands.metrics_history import collect_metrics
        with open_db(readonly=True, project_root=project_root) as conn:
            health_summary = collect_metrics(conn)
    except Exception:
        pass

    # 6. Output
    if json_mode:
        click.echo(to_json(json_envelope("init",
            summary={
                "created": created,
                "skipped": skipped,
                "had_index": had_index,
                "health_score": health_summary.get("health_score"),
            },
            created=created,
            skipped=skipped,
            had_index=had_index,
            health=health_summary,
        )))
        return

    # Text output
    created_lines = []
    for path in created:
        pad = " " * (30 - len(path))
        if path.endswith("fitness.yaml"):
            created_lines.append(f"  {path}{pad}-- Architectural rules")
        elif path.endswith("roam.yml"):
            created_lines.append(f"  {path}{pad}-- CI workflow")
        else:
            created_lines.append(f"  {path}")

    if skipped:
        for path in skipped:
            created_lines.append(f"  {path} (already exists, skipped)")

    welcome = _WELCOME.format(
        created_lines="\n".join(created_lines) if created_lines else "  (nothing new — all files already exist)",
    )
    click.echo(welcome)

    if health_summary:
        click.echo(f"\nHealth: {health_summary.get('health_score', '?')}/100  "
                    f"({health_summary.get('files', 0)} files, "
                    f"{health_summary.get('symbols', 0)} symbols, "
                    f"{health_summary.get('cycles', 0)} cycles)")
