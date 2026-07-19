"""Repo-local agent memory CLI.

Three subcommands:

  - ``roam memory add``      -- append a memory entry to .roam/memory.jsonl
  - ``roam memory list``     -- stream stored entries (optionally filtered)
  - ``roam memory relevant`` -- rank entries against a query / symbol / file

Memory is intentionally portable across agent vendors: the JSONL file
lives at the repo root under ``.roam/`` and is the SUBSTRATE that
higher-level agent-OS features (run ledger R20, constitution R24)
build on top.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam memory`` operates on substrate state in ``.roam/``
(memory.jsonl entries) — not code locations or per-location violations.
The state is consumed by other roam commands + agent runtimes directly
from disk; SARIF would be redundant. See action.yml _SUPPORTED_SARIF
allowlist + W1181-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.db.connection import find_project_root
from roam.memory.store import (
    VALID_CONFIDENCES,
    VALID_KINDS,
    MemoryEntry,
    add_memory,
    list_memory,
    memory_path,
    relevant_memory,
)
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="memory",
    category="setup",
    summary="Repo-local agent memory: add, list, and rank entries by query.",
    inputs=[],
    outputs=["entries"],
    examples=["roam memory add", "roam memory list", "roam memory relevant 'auth'"],
    tags=["memory", "agent-os"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.group("memory")
@click.pass_context
def memory_group(ctx):
    """Repo-local agent memory.

    Stored at ``.roam/memory.jsonl`` -- one JSON object per line.
    Portable across agent vendors (Claude / Cursor / Copilot / human).
    Distinct from per-vendor agent memory: lives with the repo, travels
    with checkouts, and is the substrate for the broader agent-OS.
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# memory add
# ---------------------------------------------------------------------------


@memory_group.command("add")
@click.option(
    "--kind",
    required=True,
    type=click.Choice(sorted(VALID_KINDS)),
    help="Entry kind. fact|convention|warning|decision|context.",
)
@click.option("--subject", required=True, help="What this memory is about (file path, symbol, or topic).")
@click.option("--body", required=True, help="The memory content itself.")
@click.option("--agent", default="human", show_default=True, help="Author identifier (agent name or 'human').")
@click.option(
    "--confidence",
    default="medium",
    show_default=True,
    type=click.Choice(sorted(VALID_CONFIDENCES)),
    help="Confidence in this memory.",
)
@click.option(
    "--tags",
    "tag_csv",
    default="",
    help="Comma-separated tags (e.g. 'auth,security'). Whitespace tolerated.",
)
@click.option(
    "--symbol",
    "symbols",
    multiple=True,
    help="Symbol relevance signal (repeatable).",
)
@click.option(
    "--path",
    "files",
    multiple=True,
    help="File path relevance signal (repeatable).",
)
@click.option(
    "--file",
    "files",
    multiple=True,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option(
    "--topic",
    "topics",
    multiple=True,
    help="Topic relevance signal (repeatable).",
)
@click.pass_context
def memory_add(ctx, kind, subject, body, agent, confidence, tag_csv, symbols, files, topics):
    """Append a memory entry to .roam/memory.jsonl."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    tags = [t.strip() for t in tag_csv.split(",") if t.strip()] if tag_csv else []

    try:
        entry = MemoryEntry(
            kind=kind,
            subject=subject,
            body=body,
            agent=agent,
            confidence=confidence,
            tags=tags,
            relevance_signals={
                "symbols": list(symbols),
                "files": list(files),
                "topics": list(topics),
            },
        )
    except ValueError as exc:
        verdict = f"error: {exc}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "memory-add",
                        summary={"verdict": verdict, "partial_success": True, "added": False},
                    )
                )
            )
            ctx.exit(2)
        click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)

    root = find_project_root()
    entry_id = add_memory(root, entry)

    verdict = f"added memory {entry_id} (kind={entry.kind} subject={entry.subject})"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "memory-add",
                    summary={
                        "verdict": verdict,
                        "partial_success": False,
                        "added": True,
                        "id": entry_id,
                    },
                    budget=token_budget,
                    entry=entry.to_dict(),
                    path=str(memory_path(root)),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"  id:       {entry_id}")
    click.echo(f"  ts:       {entry.ts}")
    click.echo(f"  kind:     {entry.kind}")
    click.echo(f"  subject:  {entry.subject}")
    click.echo(f"  agent:    {entry.agent}")
    if entry.tags:
        click.echo(f"  tags:     {', '.join(entry.tags)}")
    click.echo(f"  stored:   {memory_path(root)}")


# ---------------------------------------------------------------------------
# memory list
# ---------------------------------------------------------------------------


@memory_group.command("list")
@click.option("--since", default=None, help="Filter to entries with ts >= <SINCE> (ISO-8601).")  # W1117-followup
@click.option(
    "--kind",
    default=None,
    type=click.Choice(sorted(VALID_KINDS)),
    help="Filter to entries of this kind.",
)
@click.option("--top", default=0, type=int, help="Cap output to <N> entries (0 = no cap).")  # W1117-followup
@click.pass_context
def memory_list(ctx, since, kind, top):
    """Stream stored memory entries.

    Returns an empty-but-well-formed envelope when no memory exists yet
    (``state: no_memory``) -- never an error or empty stdout.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    root = find_project_root()

    path = memory_path(root)
    if not path.exists():
        verdict = "no memory yet -- run `roam memory add` to store the first entry"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "memory-list",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_memory",
                            "total": 0,
                        },
                        budget=token_budget,
                        entries=[],
                        path=str(path),
                        # W20.6 error-msg consistency
                        agent_contract={
                            "facts": ["no memory entries stored for this repo"],
                            "next_commands": ["roam memory add --kind fact --subject TOPIC --body TEXT"],
                        },
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    entries = list(list_memory(root, since=since, kind=kind))
    if top > 0:
        entries = entries[:top]

    total = len(entries)
    verdict = f"{total} memory entr{'y' if total == 1 else 'ies'}"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "memory-list",
                    summary={
                        "verdict": verdict,
                        "partial_success": False,
                        "state": "ok",
                        "total": total,
                    },
                    budget=token_budget,
                    entries=[e.to_dict() for e in entries],
                    path=str(path),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not entries:
        click.echo("  (no entries match the filter)")
        return
    rows = []
    for e in entries:
        body_preview = (e.body[:60] + "...") if len(e.body) > 63 else e.body
        rows.append([e.id, e.ts, e.kind, e.subject, e.agent, body_preview])
    click.echo(format_table(["Id", "Ts", "Kind", "Subject", "Agent", "Body"], rows))


# ---------------------------------------------------------------------------
# memory relevant
# ---------------------------------------------------------------------------


@memory_group.command("relevant")
@click.option("--query", default="", help="Free-text query.")
@click.option("--symbol", "symbols", multiple=True, help="Symbol name to match (repeatable).")
@click.option("--path", "files", multiple=True, help="File path to match (repeatable).")
@click.option(
    "--file",
    "files",
    multiple=True,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.option("--top", default=5, type=int, show_default=True, help="Return at most <N> entries.")  # W1117-followup
@click.pass_context
def memory_relevant(ctx, query, symbols, files, top):
    """Rank stored memory entries against a query / symbol / file.

    Uses set-overlap scoring -- no embeddings, no network calls. Anchor
    explicit ``--symbol`` / ``--path`` signals to skew the ranker
    toward the caller's intent.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    root = find_project_root()
    path = memory_path(root)

    if not path.exists():
        verdict = "no memory yet -- run `roam memory add` to store the first entry"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "memory-relevant",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "state": "no_memory",
                            "total": 0,
                        },
                        budget=token_budget,
                        results=[],
                        query={
                            "text": query,
                            "symbols": list(symbols),
                            "files": list(files),
                            "top": top,
                        },
                        path=str(path),
                        # W20.6 error-msg consistency
                        agent_contract={
                            "facts": ["no memory entries stored for this repo"],
                            "next_commands": ["roam memory add --kind fact --subject TOPIC --body TEXT"],
                        },
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    ranked = relevant_memory(
        root,
        query=query,
        symbols=list(symbols),
        files=list(files),
        top=top,
    )

    total = len(ranked)
    if total == 0:
        verdict = "no relevant memory found for the given query/symbol/file"
    else:
        verdict = f"{total} relevant memor{'y' if total == 1 else 'ies'} (top score {ranked[0][1]:.2f})"

    if json_mode:
        results = [{"score": round(score, 4), "entry": entry.to_dict()} for entry, score in ranked]
        click.echo(
            to_json(
                json_envelope(
                    "memory-relevant",
                    summary={
                        "verdict": verdict,
                        "partial_success": False,
                        "state": "ok",
                        "total": total,
                    },
                    budget=token_budget,
                    results=results,
                    query={
                        "text": query,
                        "symbols": list(symbols),
                        "files": list(files),
                        "top": top,
                    },
                    path=str(path),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not ranked:
        return
    rows = []
    for entry, score in ranked:
        body_preview = (entry.body[:60] + "...") if len(entry.body) > 63 else entry.body
        rows.append([f"{score:.2f}", entry.id, entry.kind, entry.subject, body_preview])
    click.echo(format_table(["Score", "Id", "Kind", "Subject", "Body"], rows))
