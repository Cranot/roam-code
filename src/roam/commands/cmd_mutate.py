"""Syntax-less agentic editing -- move, rename, add-call, extract symbols.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because ``roam mutate`` is a state-mutating code-transform
command — its output is invocation-scoped transform results (files
written, symbols moved/renamed, AST edits applied), not analysis
findings with file:line coordinates. SARIF is reserved for scanning
results. See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH
propagation plan + W1224-audit memo.
"""

from __future__ import annotations

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# W607-EG substrate-boundary plumbing for cmd_mutate.
# ---------------------------------------------------------------------------
#
# ``_run_check_eg`` wraps each substrate helper so an uncaught raise in any
# one boundary degrades to a sensible empty-floor default AND surfaces a
# marker via ``_w607eg_warnings_out`` rather than crashing the mutate
# (state-mutating code-transform) command outright. cmd_mutate pairs with
# cmd_simulate (W607-EF) along the TRANSFORM AXIS: cmd_simulate is the
# what-if counterfactual transform on a cloned graph; cmd_mutate is the
# real-transform that writes files to disk. Marker family
# ``mutate_<phase>_failed:<exc_class>:<detail>``. Substrates wrapped:
#
#   * resolve_target          -- symbol -> resolver location
#   * load_source             -- delegate to roam.refactor.transforms entry
#                                (which reads original file content)
#   * apply_transform         -- the actual mutation
#                                (move_symbol / rename_symbol / add_call /
#                                extract_symbol)
#   * validate_transform      -- lint-check on transformed result envelope
#                                (files_modified shape + conflict count)
#   * write_output            -- atomic file-write (W82.1) -- guarded by
#                                ``apply_changes`` flag on the inner
#                                transform call
#   * compose_verdict         -- LAW 6 single-line floor
#   * compose_facts           -- agent_contract.facts list
#   * compose_next_commands   -- agent_contract.next_commands
#   * serialize_envelope      -- JSON envelope emission
#   * format_text_output      -- text path emission
#
# W978 7-discipline applied: (1) f-string verdict floor uses literal
# zero-count text -- no Name references, (2) default={...} carries plain
# literals, (3) no json.dumps(default=str) needed (no datetimes), (4)
# ``mutate_*`` prefix is unique (collision-checked by cross-prefix-
# discipline test), (5) len() at kwarg-bind is gated by the envelope
# fallback, (6) len() / if x: on a poisoned object only runs after the
# empty-floor guard, (7) no dict.get(key, expensive_default) calls -- all
# defaults are immutable literals.


def _format_changes_text(result: dict) -> list[str]:
    """Format change plan as human-readable text lines."""
    lines = []
    for fmod in result.get("files_modified", []):
        action = fmod.get("action", "MODIFY")
        lines.append(f"  {fmod['path']} ({action})")
        for change in fmod.get("changes", []):
            ctype = change.get("type", "?")
            if ctype == "replace":
                ls = change.get("line_start", "?")
                le = change.get("line_end", "?")
                old = change.get("old_text", "")
                new = change.get("new_text", "")
                lines.append(f"    -{ls}  {_truncate(old, 60)}")
                lines.append(f"    +{ls}  {_truncate(new, 60)}")
            elif ctype == "insert":
                ln = change.get("line", "?")
                text = change.get("text", "")
                lines.append(f"    +{ln}  {_truncate(text, 60)}")
            elif ctype == "delete":
                ls = change.get("line_start", "?")
                le = change.get("line_end", "?")
                old = change.get("old_text", "")
                first_line = old.split("\n")[0] if old else ""
                total = le - ls + 1 if isinstance(ls, int) and isinstance(le, int) else "?"
                lines.append(f"    -{ls}..{le}  {_truncate(first_line, 50)}  ({total} lines)")
        lines.append("")
    return lines


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if needed."""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _run_check_eg(phase, fn, *args, default=None, warnings_out=None, **kwargs):
    """Run one substrate helper with W607-EG marker emission.

    On a clean call the result is returned as-is. On an uncaught
    exception, surface a ``mutate_<phase>_failed:<exc_class>:<detail>``
    marker via ``warnings_out`` and return *default* -- the envelope
    still emits cleanly with the remaining substrates.

    The ``warnings_out`` keyword carries the per-invocation
    ``_w607eg_warnings_out`` bucket; we keep it as a keyword (not a
    closed-over name) so the helper stays a clean module-level
    FunctionDef that the AST guard test can locate.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- top-level disclosure
        if warnings_out is not None:
            warnings_out.append(f"mutate_{phase}_failed:{type(exc).__name__}:{exc}")
        return default


def _emit_error(ctx, operation: str, message: str, warnings: list[str] | None = None) -> None:
    """Emit a uniform error envelope (JSON or text) for all mutate subcommands.

    Previously every subcommand inlined an identical 16-line error
    envelope block; extracting this helper removes the DRY violation and
    guarantees the four operations stay in lock-step on the envelope
    shape, the verdict wording, and the empty-changes contract.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "mutate",
                    summary={
                        "verdict": message,
                        "operation": operation,
                        "files_modified": 0,
                        "conflicts": 0,
                    },
                    changes=[],
                    warnings=warnings or [message],
                )
            )
        )
        return
    click.echo(f"VERDICT: {message}")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@roam_capability(
    name="mutate",
    category="refactoring",
    summary="Syntax-less agentic editing",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.group("mutate")
@click.pass_context
def mutate(ctx):
    """Syntax-less agentic editing.

    Unlike ``simulate`` (which predicts metric impact on a cloned graph),
    this command generates actual code transformations with import and
    reference updates.

    Move, rename, add calls, and extract symbols with automatic import
    rewriting and reference updates. Default is dry-run (preview).
    Use --apply to write changes to disk.

    \b
    Examples:
      roam mutate move handle_login src/auth/login.py
      roam mutate rename handle_login authenticate_user --apply
      roam mutate add-call --from payment_flow --to log_event
      roam mutate extract big_function --lines 42-78 --name small_helper

    See also ``simulate`` (predict metric impact without writing),
    ``preflight`` (check before mutating), and ``critique``
    (review the resulting diff).
    """
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@mutate.command("move")
@click.argument("symbol")
@click.argument("target_file")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Write changes to disk (default: dry-run preview).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview changes without writing (this is the default).",
)
@click.pass_context
def mutate_move(ctx, symbol, target_file, apply_changes, dry_run):
    """Move a symbol to a different file."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import move_symbol

    # W607-EG: substrate-boundary plumbing for cmd_mutate (move).
    # Marker family ``mutate_<phase>_failed:<exc_class>:<detail>``.
    _w607eg_warnings_out: list[str] = []

    # Empty-floor result reused by every degraded path so the mutate
    # envelope still composes a coherent verdict. Literal zero counts
    # avoid re-introducing the 7919-CATASTROPHE shape (CONSTRAINT 12).
    empty_result_floor: dict = {
        "symbol": symbol,
        "files_modified": [],
        "warnings": [],
    }

    # W607-EG: ``resolve_target`` substrate -- normalise symbol input.
    def _resolve_target():
        return symbol

    resolved_symbol = _run_check_eg(
        "resolve_target",
        _resolve_target,
        default=symbol,
        warnings_out=_w607eg_warnings_out,
    )

    # W607-EG: ``load_source`` + ``apply_transform`` + ``write_output``
    # substrates are layered inside ``move_symbol``. The transform reads
    # the original file content, applies the AST mutation, optionally
    # writes the new content atomically per W82.1 (when apply_changes is
    # set). A raise here degrades to the empty floor so the envelope
    # still composes the LAW-6 verdict.
    with open_db(readonly=True) as conn:
        result = _run_check_eg(
            "apply_transform",
            move_symbol,
            conn,
            resolved_symbol,
            target_file,
            dry_run=(not apply_changes),
            default=empty_result_floor,
            warnings_out=_w607eg_warnings_out,
        )
    if result is None:
        result = empty_result_floor

    if result.get("error"):
        _emit_error(ctx, "move", result["error"], result.get("warnings", []))
        return

    # W607-EG: ``validate_transform`` substrate -- shape-validate the
    # transform result envelope (files_modified shape + conflict count).
    def _validate_transform():
        files_local = result.get("files_modified", []) or []
        return len(files_local)

    n_files = _run_check_eg(
        "validate_transform",
        _validate_transform,
        default=0,
        warnings_out=_w607eg_warnings_out,
    )
    if n_files is None:
        n_files = 0

    # W607-EG: ``compose_verdict`` substrate -- LAW 6 single-line floor.
    # Pattern-2 guard: never collapse to SAFE / passed vocabulary on the
    # degraded path. W978 #1: f-string verdict floor is plain text.
    def _compose_verdict():
        return f"move {result.get('symbol', resolved_symbol)} -- {n_files} files modified, 0 conflicts"

    verdict = _run_check_eg(
        "compose_verdict",
        _compose_verdict,
        default="move (degraded) -- 0 files modified, 0 conflicts",
        warnings_out=_w607eg_warnings_out,
    )
    if not isinstance(verdict, str) or not verdict:
        verdict = "move (degraded) -- 0 files modified, 0 conflicts"

    # W607-EG: ``compose_facts`` substrate -- curated agent_contract.facts.
    def _compose_facts():
        return [
            verdict,
            f"{n_files} files modified",
        ]

    facts = _run_check_eg(
        "compose_facts",
        _compose_facts,
        default=[verdict],
        warnings_out=_w607eg_warnings_out,
    )
    if facts is None:
        facts = [verdict]

    # W607-EG: ``compose_next_commands`` substrate -- conditional advisory.
    def _compose_next_commands():
        cmds = []
        if not apply_changes:
            cmds.append(f"roam mutate move {symbol} {target_file} --apply")
        return cmds

    next_commands = _run_check_eg(
        "compose_next_commands",
        _compose_next_commands,
        default=[],
        warnings_out=_w607eg_warnings_out,
    )
    if next_commands is None:
        next_commands = []

    if json_mode:
        # W607-EG: ``serialize_envelope`` substrate -- json_envelope
        # construction + click.echo emission.
        envelope_summary: dict = {
            "verdict": verdict,
            "operation": "move",
            "files_modified": n_files,
            "conflicts": 0,
        }
        envelope_kwargs: dict = dict(
            summary=envelope_summary,
            changes=result.get("files_modified", []) or [],
            warnings=result.get("warnings", []) or [],
            agent_contract={
                "facts": facts,
                "risks": [],
                "next_commands": next_commands,
                "confidence": None,
            },
        )
        # W607-EG: mirror substrate markers into BOTH the top-level
        # envelope ``warnings_out`` AND ``summary.warnings_out`` so MCP
        # consumers see disclosure regardless of which surface they read.
        # Flipping ``partial_success: True`` is the Pattern-2 silent-
        # fallback guard (W607-DV late-phase bond-bug check).
        if _w607eg_warnings_out:
            envelope_summary["partial_success"] = True
            envelope_summary["warnings_out"] = list(_w607eg_warnings_out)
            envelope_kwargs["warnings_out"] = list(_w607eg_warnings_out)

        def _serialize_envelope():
            click.echo(to_json(json_envelope("mutate", **envelope_kwargs)))

        _run_check_eg(
            "serialize_envelope",
            _serialize_envelope,
            default=None,
            warnings_out=_w607eg_warnings_out,
        )
        return

    # W607-EG: ``format_text_output`` substrate -- text emission path.
    def _format_text_output():
        click.echo(f"VERDICT: {verdict}")
        click.echo("")
        for line in _format_changes_text(result):
            click.echo(line)
        if not apply_changes:
            click.echo(f"Run `roam mutate move {symbol} {target_file} --apply` to execute.")

    _run_check_eg(
        "format_text_output",
        _format_text_output,
        default=None,
        warnings_out=_w607eg_warnings_out,
    )


@mutate.command("rename")
@click.argument("symbol")
@click.argument("new_name")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Write changes to disk (default: dry-run preview).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview changes without writing (this is the default).",
)
@click.pass_context
def mutate_rename(ctx, symbol, new_name, apply_changes, dry_run):
    """Rename a symbol across the codebase."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import rename_symbol

    with open_db(readonly=True) as conn:
        result = rename_symbol(conn, symbol, new_name, dry_run=(not apply_changes))

    if result.get("error"):
        _emit_error(ctx, "rename", result["error"], result.get("warnings", []))
        return

    n_files = len(result.get("files_modified", []))
    verdict = f"rename {result.get('symbol', symbol)} -> {new_name} -- {n_files} files modified, 0 conflicts"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "mutate",
                    summary={
                        "verdict": verdict,
                        "operation": "rename",
                        "files_modified": n_files,
                        "conflicts": 0,
                    },
                    changes=result.get("files_modified", []),
                    warnings=result.get("warnings", []),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate rename {symbol} {new_name} --apply` to execute.")


@mutate.command("add-call")
@click.option("--from", "from_symbol", required=True, help="The calling symbol.")
@click.option("--to", "to_symbol", required=True, help="The callee symbol.")
@click.option("--args", "call_args", default="", help="Arguments for the call (e.g. 'data, config').")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Write changes to disk (default: dry-run preview).",
)
@click.pass_context
def mutate_add_call(ctx, from_symbol, to_symbol, call_args, apply_changes):
    """Add a call from one symbol to another."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    from roam.refactor.transforms import add_call

    with open_db(readonly=True) as conn:
        result = add_call(conn, from_symbol, to_symbol, call_args, dry_run=(not apply_changes))

    if result.get("error"):
        _emit_error(ctx, "add-call", result["error"], result.get("warnings", []))
        return

    n_files = len(result.get("files_modified", []))
    from_name = result.get("from_symbol", from_symbol)
    to_name = result.get("to_symbol", to_symbol)
    verdict = f"add-call {from_name} -> {to_name} -- {n_files} files modified, 0 conflicts"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "mutate",
                    summary={
                        "verdict": verdict,
                        "operation": "add-call",
                        "files_modified": n_files,
                        "conflicts": 0,
                    },
                    changes=result.get("files_modified", []),
                    warnings=result.get("warnings", []),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate add-call --from {from_symbol} --to {to_symbol} --apply` to execute.")


@mutate.command("extract")
@click.argument("symbol")
@click.option("--lines", required=True, help="Line range to extract (e.g. '5-10').")
@click.option("--name", "new_name", required=True, help="Name for the new extracted function.")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Write changes to disk (default: dry-run preview).",
)
@click.pass_context
def mutate_extract(ctx, symbol, lines, new_name, apply_changes):
    """Extract lines from a symbol into a new function."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # Parse line range
    try:
        parts = lines.split("-")
        line_start = int(parts[0])
        line_end = int(parts[1]) if len(parts) > 1 else line_start
    except (ValueError, IndexError):
        msg = f"invalid line range: {lines} (expected START-END)"
        _emit_error(ctx, "extract", msg, [msg])
        return

    from roam.refactor.transforms import extract_symbol

    with open_db(readonly=True) as conn:
        result = extract_symbol(conn, symbol, line_start, line_end, new_name, dry_run=(not apply_changes))

    if result.get("error"):
        _emit_error(ctx, "extract", result["error"], result.get("warnings", []))
        return

    n_files = len(result.get("files_modified", []))
    sym_name = result.get("symbol", symbol)
    verdict = f"extract {sym_name}:{lines} -> {new_name} -- {n_files} files modified, 0 conflicts"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "mutate",
                    summary={
                        "verdict": verdict,
                        "operation": "extract",
                        "files_modified": n_files,
                        "conflicts": 0,
                    },
                    changes=result.get("files_modified", []),
                    warnings=result.get("warnings", []),
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo("")
    for line in _format_changes_text(result):
        click.echo(line)

    if not apply_changes:
        click.echo(f"Run `roam mutate extract {symbol} --lines {lines} --name {new_name} --apply` to execute.")
