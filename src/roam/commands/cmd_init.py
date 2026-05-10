"""Initialize a project for Roam: index + config (no unsolicited CI)."""

from __future__ import annotations

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import db_exists, find_project_root
from roam.output.formatter import json_envelope, to_json

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

# Conservative .roamignore template — every line commented out so the
# user opts in to whatever applies. Faster to remove a `#` than to
# rebuild the list.
_ROAMIGNORE_TEMPLATE = """\
# Roam ignore — gitignore syntax. Skip directories that bloat the
# index without adding signal. Uncomment lines that apply to your repo.

# Build outputs
# dist/
# build/
# out/
# target/
# .next/
# .nuxt/

# Vendored deps
# node_modules/
# vendor/
# bower_components/

# Python virtualenvs + caches
# .venv/
# venv/
# __pycache__/
# .mypy_cache/
# .pytest_cache/

# Coverage / profiling
# coverage/
# htmlcov/
# .coverage

# Generated assets
# *.min.js
# *.min.css
"""

_WELCOME = """\
Roam is ready: {files} files, {symbols} symbols, {edges} edges. Health: {health}/100.

Try one:    roam understand                    (briefing)
Next:       git diff | roam critique           (the killer demo)
Help:       roam ask "<question>"              roam --help
Wire MCP:   roam mcp-setup <claude|cursor|codex|gemini|amp>"""


def _is_inside_git_repo(project_root) -> bool:
    """Return True when ``project_root`` (or any ancestor) has a ``.git``.

    ``find_project_root`` already returns a usable path, but it falls
    back to ``cwd`` when no .git is present — that's the spawn-in-Downloads
    failure mode the audit flagged. Refuse to init outside a repo.
    """
    p = project_root.resolve()
    while True:
        if (p / ".git").exists():
            return True
        if p.parent == p:
            return False
        p = p.parent


@click.command("init")
@click.option("--root", default=".", help="Project root")
@click.option("--yes", is_flag=True, help="Non-interactive, accept defaults")
@click.option(
    "--with-ci",
    "with_ci",
    type=click.Choice(["github"], case_sensitive=False),
    default=None,
    help=(
        "Generate a CI workflow for the named platform. Default is no "
        "CI write — explicit opt-in is required so `roam init` doesn't "
        "drop foreign config into a repo the user is just evaluating. "
        "For full multi-platform CI generation see `roam ci-setup`."
    ),
)
@click.pass_context
def init(ctx, root, yes, with_ci):
    """Initialize Roam for this project: index + config.

    Indexes the project, creates ``.roam/`` config directory with
    ``fitness.yaml``, and (if absent) writes a starter ``.roamignore``
    template at the project root. Refuses to run outside a git
    repository — pass ``--with-ci=github`` to opt into a starter
    GitHub Actions workflow. For full multi-platform CI generation
    see ``roam ci-setup``.

    \b
    Examples:
      roam init                      # interactive bootstrap
      roam init --yes                # accept all defaults
      roam init --with-ci=github     # also drop a starter Actions workflow

    See also ``doctor`` (validates the index after init), ``mcp-setup``
    (configure MCP for an editor), and ``understand`` (first call after
    init for repo orientation).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    project_root = find_project_root(root)

    if not _is_inside_git_repo(project_root):
        from roam.output.errors import structured_usage_error

        raise structured_usage_error(
            "FILE_NOT_FOUND",
            "no .git directory found at or above the project root. "
            "`roam init` only runs inside a git repository — `git init` "
            "first if you genuinely want roam to track a non-git tree, or "
            "`cd` into the right project root.",
        )

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
    ensure_index(quiet=json_mode)

    # 3. Generate .roam/fitness.yaml
    fitness_path = roam_dir / "fitness.yaml"
    if not fitness_path.exists():
        fitness_path.write_text(_FITNESS_YAML, encoding="utf-8")
        created.append(".roam/fitness.yaml")
    else:
        skipped.append(".roam/fitness.yaml")

    # 4. .roamignore template — only when absent. Every line commented
    # out so the user opts in. Don't write inside .roam/ (gitignored
    # already); it lives at the project root next to .gitignore.
    roamignore_path = project_root / ".roamignore"
    if not roamignore_path.exists():
        roamignore_path.write_text(_ROAMIGNORE_TEMPLATE, encoding="utf-8")
        created.append(".roamignore")
    else:
        skipped.append(".roamignore")

    # 5. CI workflow — opt-in only. Audit R1: dropping foreign config
    # files into the user's repo on first command was the single
    # biggest churn driver. ``roam ci-setup`` is the canonical place
    # for full multi-platform generation; this flag is just the
    # one-line shortcut.
    if with_ci == "github":
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
        from roam.commands.metrics_history import collect_metrics
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=project_root) as conn:
            health_summary = collect_metrics(conn)
    except Exception:
        pass

    # 6. Output
    _files = health_summary.get("files", 0)
    _symbols = health_summary.get("symbols", 0)
    _edges = health_summary.get("edges", 0)
    _verdict = f"initialized: {_files} files, {_symbols} symbols, {_edges} edges"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "init",
                    summary={
                        "verdict": _verdict,
                        "created": created,
                        "skipped": skipped,
                        "had_index": had_index,
                        "health_score": health_summary.get("health_score"),
                    },
                    created=created,
                    skipped=skipped,
                    had_index=had_index,
                    health=health_summary,
                )
            )
        )
        return

    # Text output — compact welcome banner per audit R10. Older banner
    # was 20+ lines of agent-contract teaching at a moment when the
    # user just wants to see "did it work?" The contract belongs in
    # docs and SKILL.md, not on every init.
    welcome = _WELCOME.format(
        files=health_summary.get("files", 0),
        symbols=health_summary.get("symbols", 0),
        edges=health_summary.get("edges", 0),
        health=health_summary.get("health_score", "?"),
    )
    click.echo(f"VERDICT: {_verdict}\n")
    click.echo(welcome)

    if created:
        click.echo("\nCreated:")
        for path in created:
            click.echo(f"  {path}")
