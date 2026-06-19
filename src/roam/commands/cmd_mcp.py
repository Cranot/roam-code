"""Fast-startup wrapper around the underlying MCP server command.

The real MCP server lives in ``roam.mcp_server`` (an 8600+ line module).
A user-visible measurement in ``internal/dogfood/evals/mcp/2026-05-12-startup-timeout.md``
showed ``roam mcp`` blocking ~38 seconds before ``mcp.run()`` — long
enough to blow past the default 30 s MCP-client connect timeout on
codebases with non-trivial indexes. ~36 s of that was a synchronous
``_ensure_fresh_index('.')`` call which shells out to ``roam index``
(full incremental reindex) regardless of whether the index is actually
stale.

This wrapper short-circuits that path:

* ``--help``, ``--list-tools``, ``--list-tools-json``, ``--compat-profile``
  and ``--card`` are pure info modes — we delegate verbatim. The
  underlying ``mcp_cmd`` returns before the freshness check for those.

* For the server start path (the common case: ``roam mcp`` on stdio for
  Claude Code, Cursor, etc.) we replace the heavy reindex with a fast
  mtime-only ``check_stale`` call (typically <100 ms). If the index
  looks fresh, we hand off to the underlying command with
  ``no_auto_index=True`` so it skips its own reindex. If it looks stale,
  we emit a stderr warning telling the user to run ``roam index`` and
  still hand off with ``no_auto_index=True`` — the safety net becomes
  a visible affordance rather than a 36 s connect-timeout bomb.

* If the user passed ``--no-auto-index`` themselves we don't run the
  fast check either; their intent is explicit.

NOTE: the wrapper deliberately avoids importing ``roam.mcp_server`` at
module load time. ``roam.cli.LazyGroup`` already only imports the
target module when ``get_command`` is called, but doing the import
inside the function body keeps the cost paid exactly once at the right
time (server invocation) instead of leaking across ``--help`` /
shell-completion paths some users wire into prompts.

Output formats: stdio MCP protocol (server start) or text/JSON for info
modes (``--list-tools``, ``--list-tools-json``, ``--card``).
SARIF is deliberately NOT emitted because ``roam mcp`` is a setup /
bootstrap / daemon command — its output is either the MCP wire
protocol or human-facing setup status (tool inventory, capability
cards), not analysis findings with file:line coordinates. SARIF is
reserved for scanning results. See action.yml _SUPPORTED_SARIF
allowlist + W1175-RESEARCH propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import sys

import click


@click.command(name="mcp")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="transport protocol (default: stdio)",
)
@click.option("--host", default="127.0.0.1", help="host for network transports")
@click.option("--port", type=int, default=8000, help="port for network transports")
@click.option(
    "--no-auto-index",
    is_flag=True,
    help="skip automatic index freshness check (fast boot for MCP clients with short timeouts)",
)
@click.option("--list-tools", is_flag=True, help="list registered tools and exit")
@click.option(
    "--list-tools-json",
    is_flag=True,
    help="list registered tools with metadata as JSON and exit",
)
@click.option(
    "--compat-profile",
    type=click.Choice(["all", "claude", "codex", "gemini", "copilot", "vscode", "cursor"]),
    default=None,
    help="emit client compatibility profile JSON and exit",
)
@click.option(
    "--card",
    is_flag=True,
    help=(
        "Print the MCP Server Card (the .well-known/mcp-server-card.json "
        "shape per spec 2025-11-25). Useful for piping into registry "
        "submissions: ``roam mcp --card | jq .``."
    ),
)
def mcp(transport, host, port, no_auto_index, list_tools, list_tools_json, compat_profile, card):
    """Start the roam MCP server.

    \b
    usage:
      roam mcp                    # stdio (for Claude Code, Cursor, etc.)
      roam mcp --transport sse    # SSE on localhost:8000
      roam mcp --transport streamable-http  # Streamable HTTP on localhost:8000
      roam mcp --list-tools       # show registered tools
      roam mcp --list-tools-json  # JSON metadata for conformance checks
      roam mcp --compat-profile all  # client compatibility matrix (JSON)

    \b
    environment:
      ROAM_MCP_PRESET=core        # tool preset (core/review/refactor/debug/architecture/full)
      ROAM_MCP_LITE=0             # legacy: same as ROAM_MCP_PRESET=full

    \b
    integration:
      claude mcp add roam-code -- roam mcp

    \b
    requires:
      pip install roam-code[mcp]
    """
    # Pure info modes: delegate verbatim. The underlying mcp_cmd returns
    # before any freshness check in these branches, so there's no extra
    # cost to pay here.
    info_only = bool(card or list_tools or list_tools_json or compat_profile)

    # Decide whether to substitute a fast freshness check for the heavy
    # ``_ensure_fresh_index('.')`` reindex. We only intervene on the
    # server-start path AND only when the user hasn't already opted out
    # via ``--no-auto-index``.
    skip_heavy_reindex = False
    if not info_only and not no_auto_index:
        try:
            from roam.commands.stale_index import check_stale

            is_stale, reason = check_stale(sensitivity="medium")
        except (ImportError, OSError):
            # Defensive: if the fast check cannot import or read the local
            # index, keep booting without paying the heavy reindex cost.
            is_stale, reason = False, None

        if is_stale:
            click.echo(
                f"warning: index appears stale — {reason}. "
                "Run `roam index` to refresh. Booting server without auto-reindex.",
                err=True,
            )
        # Either way: the fast check has already told us what we need,
        # so we skip the slow reindex. The agent / IDE will get the
        # stale-banner affordance on individual tool responses
        # (mcp_server._annotate_stale already handles that).
        skip_heavy_reindex = True

    # Lazy import: keep the 8600-line mcp_server module out of any code
    # path that doesn't actually need it. (LazyGroup already gives us
    # this for free at the command level — this is belt-and-braces in
    # case someone imports cmd_mcp directly.)
    from roam.mcp_server import mcp_cmd as _real_mcp_cmd

    effective_no_auto_index = no_auto_index or skip_heavy_reindex

    # Click stores the decorated callback on ``.callback``. Bypass the
    # Click invocation machinery (we've already parsed args) and call
    # the underlying function directly with positional args matching
    # ``mcp_cmd``'s signature.
    return _real_mcp_cmd.callback(
        transport,
        host,
        port,
        effective_no_auto_index,
        list_tools,
        list_tools_json,
        compat_profile,
        card,
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke
    sys.exit(mcp.main(standalone_mode=False))
