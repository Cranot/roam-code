"""Initialize a project for Roam: index + config (no unsolicited CI).

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam init`` is a setup/bootstrap command — its output
is human-facing setup status (index created, config written, fitness
template stamped), not analysis findings with file:line coordinates.
SARIF is reserved for scanning results. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH propagation plan +
W1148 audit memo.
"""

from __future__ import annotations

import os

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import _is_genuine_git_marker, db_exists, find_project_root
from roam.db.fs_detect import cloud_sync_warning, detect_cloud_sync
from roam.output.formatter import json_envelope, to_json

# Self-ignoring marker for ``.roam/``. Git honours a ``.gitignore`` placed
# INSIDE the directory it ignores, so this keeps Roam's index, cache, and
# generated config out of the user's ``git status`` without ever editing a
# file the user owns. Appending ``.roam/`` to the repo's own ``.gitignore``
# was the alternative and is worse: it dirties a TRACKED file on command 2
# of the golden path (a real diff to review and commit) to silence an
# UNTRACKED one, and it silently loses the entry on any repo that keeps
# ignores in ``.git/info/exclude`` or a global excludesfile. Same pattern
# CPython's ``venv`` and Cargo's ``target/`` use. ``.roamignore`` is left
# visible on purpose — it is user-authored config in gitignore syntax,
# meant to be edited and committed like ``.dockerignore``.
_ROAM_DIR_GITIGNORE = """\
# Created by `roam init`. Everything under .roam/ is a derived artifact
# (SQLite index, caches, generated fitness config) — never commit it.
*
"""

# W1514: every key here must be one the dispatched checker READS -- see
# ``_RULE_KEYS_BY_TYPE`` in cmd_fitness. The original template used
# ``source``/``forbidden_target`` and ``threshold``, none of which any
# checker reads, so the dependency rule forbade every edge in the
# repository (``from``/``to`` both default to ``**``) and the complexity
# rule was inert (``max`` defaulted to 999 -- a measured cc=377 function
# PASSED). The first rule was also named for a check that cannot exist:
# circular imports are a ``metric: cycles`` rule, not a dependency rule.
_FITNESS_YAML = """\
rules:
  - name: Production code must not import test modules
    type: dependency
    from: "src/**"
    to: "tests/**"
    allow: false
    reason: "Production code should not depend on test infrastructure"
  - name: Complexity threshold
    type: metric
    metric: cognitive_complexity
    max: 30
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
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1
        with:
          fetch-depth: 0
          persist-credentials: false
      # The tag below is the last PUBLISHED release, kept there by
      # scripts/sync_surface_counts.py (install-pin class). Do not hand-edit
      # it to the declared version: a ref that is not yet tagged fails this
      # workflow in the USER'S repository with 'Unable to resolve action'.
      # Harden further by resolving the tag to its reviewed 40-character SHA.
      - uses: Cranot/roam-code@v14.0.1
        with:
          version: "14.0.1"
          commands: "fitness pr-risk"
          python-version: "3.12"
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
Roam is ready: {files} files, {symbols} symbols, {edges} edges.

Try one:    roam health                        (score this codebase 0-100)
            roam understand                    (briefing)
Next:       git diff | roam critique           (the killer demo)
Help:       roam ask "<question>"              roam --help
Wire MCP:   roam mcp-setup <claude-code|cursor|codex-cli|gemini-cli|vscode|windsurf>
            Preview configuration; add --write to install it."""


def _is_inside_git_repo(project_root) -> bool:
    """Validate the Git marker at the resolved bootstrap destination.

    ``find_project_root`` has already searched ancestors and rejected stray
    markers. A second, looser ancestor search can accept that rejected marker
    (or a newly visible repository) and authorize writes at the wrong root.
    Reuse canonical validation on the actual destination; this also retains
    support for worktree/submodule pointer files.
    """
    return _is_genuine_git_marker(project_root.resolve() / ".git")


@roam_capability(
    name="init",
    category="setup",
    summary="Initialize Roam for this project: index + .roam config.",
    inputs=["root"],
    outputs=["verdict"],
    examples=["roam init", "roam init --yes", "roam init --with-ci github"],
    tags=["setup", "bootstrap"],
    ai_safe=False,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.command("init")
@click.option("--root", default=".", help="Project root")
# ACCEPTED NO-OP, on purpose. `roam init` has never prompted (no click.confirm
# / click.prompt / input() anywhere in this module), so there is nothing for
# --yes to suppress. It is kept because scripts, CI recipes and the MCP
# `roam_init` wrapper all pass the conventional `--yes`, and removing it would
# break them to no benefit. Registered in scripts/dead_cli_flags_allowlist.txt
# so the dead-flag gate knows this one is deliberate rather than forgotten;
# delete the allowlist entry the day init grows a prompt.
@click.option(
    "--yes",
    is_flag=True,
    help="Accepted for scripting convenience. `roam init` is already fully non-interactive, so this changes nothing.",
)
@click.option(
    "--with-ci",
    "with_ci",
    type=click.Choice(["github", "gitlab", "none"], case_sensitive=False),
    default="none",
    show_default=True,
    help=(
        "Generate a CI workflow for the named platform. Default is "
        "``none`` — explicit opt-in is required so `roam init` doesn't "
        "drop foreign config into a repo the user is just evaluating. "
        "For full multi-platform CI generation see `roam ci-setup`."
    ),
)
@click.option(
    "--since",
    "since",
    default=None,
    help=(
        "Git history window to scan on the first index. Accepts shorthand "
        "(``365d`` / ``12m`` / ``2y``) or any git-compatible date phrase "
        '(``2025-01-01``, ``"2 weeks ago"``). Sets ``ROAM_GIT_SINCE`` for '
        "this run. Default: 365d on a brand-new index; on a warm index the "
        "skip-on-unchanged-HEAD optimisation usually means no git pull at "
        "all. Pass ``--full-history`` to disable the shallow default."
    ),
)
@click.option(
    "--full-history",
    "full_history",
    is_flag=True,
    default=False,
    help=(
        "Disable the shallow git-history default. Equivalent to "
        "``ROAM_GIT_SINCE=0``. Use this when you need full churn / blame "
        "history for cohort analysis (e.g. ``roam dev-profile``, "
        "``roam bus-factor`` over years of commits)."
    ),
)
@click.pass_context
def init(ctx, root, yes, with_ci, since, full_history):
    """Initialize Roam for this project: index + config.

    Indexes the project, creates ``.roam/`` config directory with
    ``fitness.yaml``, and (if absent) writes a starter ``.roamignore``
    template at the project root. Refuses to run outside a git
    repository. Default ``--with-ci=none`` writes no CI file; pass
    ``--with-ci=github`` to opt into a starter GitHub Actions
    workflow. For full multi-platform CI generation (including
    GitLab), see ``roam ci-setup``.

    \b
    Examples:
      roam init                      # bootstrap (never prompts)
      roam init --yes                # identical; --yes is accepted, not needed
      roam init --with-ci=github     # also drop a starter Actions workflow

    See also ``doctor`` (validates the index after init), ``mcp-setup``
    (configure MCP for an editor), and ``understand`` (first call after
    init for repo orientation).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    project_root = find_project_root(root)

    if not _is_inside_git_repo(project_root):
        from roam.output.errors import structured_usage_error

        error = structured_usage_error(
            "FILE_NOT_FOUND",
            "no valid .git marker found at the resolved project root. "
            "`roam init` only runs inside a git repository — `git init` "
            "first if you genuinely want roam to track a non-git tree, or "
            "`cd` into the right project root. "
            "If this looks unexpected, run `roam doctor` to diagnose your install.",
        )
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "init",
                        persist_response=False,
                        summary={
                            "verdict": "Initialization refused: no valid Git repository; no initialized files",
                            "state": "not_initialized",
                            "partial_success": True,
                        },
                        error_code="FILE_NOT_FOUND",
                        error=error.message,
                        created=[],
                        skipped=[],
                    )
                )
            )
            ctx.exit(error.exit_code)
        raise error

    created = []
    skipped = []
    warnings: list[dict] = []

    # 1. Create .roam/ directory
    roam_dir = project_root / ".roam"
    roam_dir.mkdir(exist_ok=True)

    # 1a. W127 — cloud-sync detection. SQLite WAL races with OneDrive /
    # Dropbox / iCloud / Google Drive sync agents on writes; the runtime
    # mitigation in ``db.connection.get_connection`` already swaps the
    # journal to DELETE + EXCLUSIVE locking, but the user should know
    # because indexing is slower and large repos can still hit transient
    # 'database is locked' errors. Surface a one-line advisory so the
    # user can opt into a local cache via ``roam config --use-local-cache``
    # or ``ROAM_DB_DIR``. Never fail init on this — strictly advisory.
    cloud_provider = detect_cloud_sync(roam_dir)
    if cloud_provider:
        warnings.append(
            {
                "code": "cloud_sync_detected",
                "provider": cloud_provider,
                "path": str(roam_dir),
                "message": cloud_sync_warning(cloud_provider, roam_dir),
                "remediation": "roam config --use-local-cache",
            }
        )

    # 2. Run indexing if no index exists.
    #
    # Shallow git-history default (W405): unless the user explicitly opted
    # out via ``--full-history`` (or set ``ROAM_GIT_SINCE`` themselves), the
    # indexer caps git log to ~365 days on the FIRST index. Smaller wallclock
    # cost, same agent-relevant signal. ``--full-history`` wins over
    # ``--since`` per LAW 11 (user intent > inference).
    if full_history:
        os.environ["ROAM_GIT_SINCE"] = "0"
    elif since:
        os.environ["ROAM_GIT_SINCE"] = since

    had_index = db_exists(project_root)
    if not had_index:
        if not json_mode:
            click.echo("No index found. Building...")
    # W1291: suppress the "Run `roam init` to create one" cold-start advisory
    # since the user just ran exactly that. cmd_init IS the init path; the
    # advisory belongs on commands that consume an index, not the one
    # building it.
    ensure_index(quiet=json_mode, suppress_cold_start_advisory=True)

    # 2b. Self-ignore .roam/ so `roam init` does not dirty `git status`
    # (and so `roam health` does not then report that dirt as index
    # staleness). Written only when absent — a user who deliberately
    # deleted it, or who committed .roam/ on purpose, keeps their choice.
    roam_gitignore_path = roam_dir / ".gitignore"
    if not roam_gitignore_path.exists():
        roam_gitignore_path.write_text(_ROAM_DIR_GITIGNORE, encoding="utf-8")
        created.append(".roam/.gitignore")
    else:
        skipped.append(".roam/.gitignore")

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
    # one-line shortcut. Normalise None → "none" so the default never
    # writes a workflow file regardless of how the option arrived.
    with_ci_norm = (with_ci or "none").lower()
    if with_ci_norm == "github":
        workflow_dir = project_root / ".github" / "workflows"
        workflow_path = workflow_dir / "roam.yml"
        if not workflow_path.exists():
            workflow_dir.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text(_GITHUB_WORKFLOW, encoding="utf-8")
            created.append(".github/workflows/roam.yml")
        else:
            skipped.append(".github/workflows/roam.yml")
    elif with_ci_norm == "gitlab":
        # Stub — full GitLab CI generation lives in ``roam ci-setup``.
        # Point the user there instead of writing a half-baked template.
        if not json_mode:
            click.echo(
                "Note: --with-ci=gitlab is not yet implemented in `init`. "
                "Run `roam ci-setup --platform gitlab` for the full GitLab CI generator."
            )

    # 5. Quick health summary
    health_summary = {}
    try:
        from roam.commands.metrics_history import collect_metrics
        from roam.db.connection import open_db

        with open_db(readonly=True, project_root=project_root) as conn:
            health_summary = collect_metrics(conn)
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_init:health_summary", _exc)

    # 5b. W1502 — the three-valued corpus verdict. ``init`` used to print
    # "Roam is ready: N files, 0 symbols, 0 edges" and exit 0 for at least
    # six structurally different situations, four of them broken. That is
    # an ABSENT measurement rendered as a definite success value, in the
    # first command a new user runs. ``corpus_state.classify`` is the one
    # boundary that distinguishes "indexed", "legitimately nothing to
    # index" and "indexing failed"; ``index`` and ``doctor`` call the same
    # function so the vocabulary cannot drift between them.
    #
    # A classifier that itself fails is a refusal, never a benign default.
    from roam.index.corpus_state import (
        STATE_FAILED,
        STATE_INDEXED,
        CorpusVerdict,
        classify_project,
    )

    try:
        # ``init`` just ran the index in this process, so the parser's error
        # counters are a measurement, not a guess. They are what separates
        # "every file parsed and declares nothing" (a docs-shaped or
        # import-only tree — legitimately zero) from "no grammar was
        # installed" (a broken index that must refuse).
        #
        # Only when it actually indexed, though. ``ensure_index`` is a no-op
        # over an existing index, and the counters are per-process, so a
        # SECOND ``roam init`` reads 0 having parsed nothing. Measured
        # 2026-08-12 on a repo whose only .py is unparseable: run 1 refused
        # (exit 1), run 2 over the identical index reported "1 file(s) …
        # parsed without error" and exited 0. Pass None when this process did
        # not parse, so the classifier re-measures rather than inheriting a
        # zero that describes nothing.
        from roam.index.parser import get_parse_error_count

        corpus = classify_project(project_root, parse_errors=None if had_index else get_parse_error_count())
    except Exception as _exc:  # noqa: BLE001 — unknown state is a refusal
        from roam.observability import log_swallowed

        log_swallowed("cmd_init:corpus_state", _exc)
        corpus = CorpusVerdict(
            state=STATE_FAILED,
            reason="index_unreadable",
            detail=(
                f"indexing failed: the index could not be inspected after the build ({type(_exc).__name__}: {_exc})"
            ),
            remediation="Run `roam doctor` to diagnose the install.",
        )

    # 6. Output. Counts come from the corpus verdict so the banner, the
    # JSON envelope and the exit code can never disagree about what was
    # actually indexed.
    _files = corpus.files
    _symbols = corpus.symbols
    _edges = corpus.edges
    if corpus.state == STATE_INDEXED:
        _verdict = f"initialized: {_files} files, {_symbols} symbols, {_edges} edges"
    else:
        # ``detail`` already opens with "no indexable content:",
        # "indexing failed:" or "index unverified:" — the zeros must not
        # read alike, and an unmeasured one must not read like a diagnosed one.
        _verdict = corpus.detail
    # Compact warning codes for the JSON summary — full structured
    # entries live in the top-level ``warnings`` field so JSON consumers
    # can branch on the code without parsing the message string.
    warning_codes = [w["code"] for w in warnings]

    summary = {
        "verdict": _verdict,
        "created": created,
        "skipped": skipped,
        "had_index": had_index,
        "health_score": health_summary.get("health_score"),
        "warnings": warning_codes,
        "state": corpus.state,
    }
    if corpus.state != STATE_INDEXED:
        summary["reason"] = corpus.reason
        summary["partial_success"] = True
    if corpus.state == STATE_FAILED:
        summary["resolution"] = "unresolved"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "init",
                    summary=summary,
                    created=created,
                    skipped=skipped,
                    had_index=had_index,
                    health=health_summary,
                    corpus=corpus.as_dict(),
                    warnings=warnings,
                )
            )
        )
        if corpus.state == STATE_FAILED:
            from roam.exit_codes import EXIT_ERROR

            ctx.exit(EXIT_ERROR)
        return

    # Text output — compact welcome banner per audit R10. Older banner
    # was 20+ lines of agent-contract teaching at a moment when the
    # user just wants to see "did it work?" The contract belongs in
    # docs and SKILL.md, not on every init.
    # W1288: drop the "Health: N/100" line. metrics_history's quick post-index
    # score diverged from cmd_health's canonical compute_health_score (different
    # cycle filtering — actionable vs raw — and missing the coverage factor),
    # so the welcome banner contradicted `roam health` run seconds later in the
    # same shell. Point the user at `roam health` for the canonical number;
    # keep the snapshots/baseline pipeline's metric alone so history is stable.
    click.echo(f"VERDICT: {_verdict}\n")

    # Warnings go BEFORE the welcome banner so the user sees them
    # before the "Try one: ..." next-steps copy. Write to stderr so
    # piped consumers (e.g. ``roam init | tee init.log``) don't fold
    # the advisory into the success transcript. (W127)
    for w in warnings:
        click.echo(w["message"], err=True)
    if warnings:
        click.echo("", err=True)

    if corpus.state == STATE_INDEXED:
        click.echo(
            _WELCOME.format(
                files=_files,
                symbols=_symbols,
                edges=_edges,
            )
        )
    else:
        # No "Roam is ready" banner over an index that can answer nothing.
        # The disclosure block names which zero this is and what caused it.
        for line in corpus.text_lines()[1:]:
            click.echo(line)

    if created:
        click.echo("\nCreated:")
        for path in created:
            click.echo(f"  {path}")

    # CI hint — only when no workflow was written this run. Keeps the
    # "did roam touch my repo?" trust contract: nothing CI-shaped lands
    # on disk unless the user explicitly asks via `--with-ci=...`.
    if with_ci_norm == "none":
        click.echo("\nTo generate CI integration: roam ci-setup")

    if corpus.state == STATE_FAILED:
        from roam.exit_codes import EXIT_ERROR

        ctx.exit(EXIT_ERROR)
