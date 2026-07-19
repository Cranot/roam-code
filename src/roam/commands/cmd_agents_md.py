"""``roam agents-md`` -- synthesize an ``AGENTS.md`` from indexed state.

The output bundles the same signals an agent would otherwise piece
together by running half a dozen commands (`describe`, `conventions`,
`hotspots --danger`, `constitution show`, `laws list`, `capabilities`).
Keeping it as a single command makes onboarding deterministic.

Implementation notes
--------------------
* Pure synthesis -- all subsystems are consulted in read-only mode
  via :func:`roam.agents_md.generator.generate_agents_md`.
* Auto-logs to the active run when ``ROAM_RUN_ID`` is set so the
  AGENTS.md generation event lands in the agent's replayable timeline.
* No expensive shell-outs to sibling commands; danger zones use the
  same single SQL query as ``roam dashboard``.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because agents-md outputs are AGENTS.md documents — not
per-location violations. SARIF is reserved for findings with file:line
coordinates; agents-md's primary deliverable is the AGENTS.md document.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket C
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.agents_md.generator import AgentsMdOptions, generate_agents_md, render_agents_markdown
from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json
from roam.runs.helpers import auto_log


@roam_capability(
    name="agents-md",
    category="setup",
    summary="Generate AGENTS.md (stack, conventions, danger zones, gates, laws, capabilities).",
    inputs=[],
    outputs=["agents_md"],
    examples=[
        "roam agents-md",
        "roam agents-md --out AGENTS.md",
        "roam agents-md --json",
        "roam agents-md --no-laws --no-rules",
        "roam agents-md --top-danger 20",
    ],
    tags=["agent-os", "documentation", "setup"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command("agents-md")
@click.option(
    "--out",
    "-o",
    "out_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write Markdown to this file. When unset, print to stdout.",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="Alias for `--out AGENTS.md`; overwrite an existing AGENTS.md at the repo root.",
)
@click.option(
    "--attribution/--no-attribution",
    "with_attribution",
    default=True,
    show_default=True,
    help="Append a hidden attribution footer to the generated AGENTS.md.",
)
@click.option(
    "--with-laws/--no-laws",
    "with_laws",
    default=True,
    show_default=True,
    help="Include the Architectural invariants section (mined from `roam laws`).",
)
@click.option(
    "--with-rules/--no-rules",
    "with_rules",
    default=True,
    show_default=True,
    help="Include the Graph-aware policy rules section (enumerates `.roam/rules/*.yml`).",
)
@click.option(
    "--with-constitution/--no-constitution",
    "with_constitution",
    default=True,
    show_default=True,
    help="Include the Workflow gates section (from `.roam/constitution.yml` or defaults).",
)
@click.option(
    "--top-danger",
    "top_danger",
    type=int,
    default=10,
    show_default=True,
    help="Maximum number of files listed in the Danger zones table.",
)
@click.option(
    "--top-laws",
    "top_laws",
    type=int,
    default=8,
    show_default=True,
    help="Maximum number of mined laws shown under Architectural invariants.",
)
@click.pass_context
def agents_md_cmd(
    ctx: click.Context,
    out_path: str | None,
    refresh: bool,
    with_attribution: bool,
    with_laws: bool,
    with_rules: bool,
    with_constitution: bool,
    top_danger: int,
    top_laws: int,
) -> None:
    """Generate an ``AGENTS.md`` describing this codebase to AI agents.

    Synthesizes conventions, danger zones, workflow gates, mined laws,
    rule files, and the capability roster into a single Markdown doc.
    Run once per repo and refresh whenever conventions or danger zones
    drift -- the canonical workflow is::

        roam agents-md --refresh   # writes/overwrites AGENTS.md
    """
    json_mode = bool(ctx.obj.get("json")) if ctx.obj else False

    # `--refresh` is a convenience alias for `--out AGENTS.md`.
    if refresh and not out_path:
        out_path = "AGENTS.md"

    ensure_index()
    try:
        repo_root = find_project_root()
    except OSError:
        repo_root = Path(".").resolve()

    with open_db(readonly=True) as conn:
        am = generate_agents_md(
            repo_root,
            conn,
            options=AgentsMdOptions(
                with_laws=with_laws,
                with_rules=with_rules,
                with_constitution=with_constitution,
                top_n_danger=top_danger,
                top_n_laws=top_laws,
            ),
        )

    markdown = render_agents_markdown(am, with_attribution=with_attribution)

    wrote_to: str | None = None
    if out_path:
        target = Path(out_path)
        if not target.is_absolute():
            target = repo_root / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
            wrote_to = str(target)
        except OSError as exc:
            # Surface a clean failure envelope rather than crashing
            # mid-write; this matches the "never silent SAFE" rule.
            verdict = f"failed to write AGENTS.md to {target}: {exc}"
            envelope = json_envelope(
                "agents-md",
                summary={
                    "verdict": verdict,
                    "state": "write_failed",
                    "partial_success": True,
                    "sections": am.section_names(),
                },
                preview=markdown[:500],
                attempted_path=str(target),
                error=str(exc),
                sources_consulted=sorted(am.sources.keys()),
                agents_md=am.to_dict(),
            )
            # auto_log is documented + verified to never raise.
            auto_log(envelope, action="agents-md", target=str(target))
            if json_mode:
                click.echo(to_json(envelope))
            else:
                click.echo(f"VERDICT: {verdict}", err=True)
            ctx.exit(1)

    section_count = len(am.section_names())
    char_count = len(markdown)
    if wrote_to:
        verdict = f"Generated AGENTS.md ({section_count} sections, {char_count} chars) -> {wrote_to}"
    else:
        verdict = (
            f"Generated AGENTS.md ({section_count} sections, {char_count} chars) -- pass --out AGENTS.md to persist"
        )

    envelope_kwargs: dict = {
        "preview": markdown[:500],
        "sources_consulted": sorted(am.sources.keys()),
        "agents_md": am.to_dict(),
    }
    if wrote_to:
        envelope_kwargs["wrote_to"] = wrote_to

    envelope = json_envelope(
        "agents-md",
        summary={
            "verdict": verdict,
            "state": "ok",
            "partial_success": False,
            "sections": am.section_names(),
            "section_count": section_count,
            "char_count": char_count,
        },
        **envelope_kwargs,
    )

    # auto_log is documented + verified to never raise.
    auto_log(envelope, action="agents-md", target=wrote_to or "")

    if json_mode:
        click.echo(to_json(envelope))
        return

    # Plain-text output: echo the rendered Markdown to stdout (unless we
    # wrote it to a file, in which case emit the verdict line).
    if wrote_to:
        click.echo(verdict)
    else:
        click.echo(markdown, nl=False)
