"""Context-enriched grep: text search annotated with the roam index.

Beyond what raw ripgrep / git grep produce, every match carries:
  * smallest enclosing symbol (single bulk fetch — no N+1)
  * reachability badge from a named entry, when --reachable-from is set
  * pagerank / heat / blame / clone-class annotations on demand
  * cross-language bridge link (e.g. `.env` key → consuming function)

Multi-pattern (-e repeatable, --patterns-from FILE), multi-glob, and
literal mode (-F) are first-class. Engine selection prefers ripgrep when
on PATH; falls back to git grep, then to indexed-file scan.

Filters:
  * --reachable-from ENTRY  keep hits inside symbols transitively reached
  * --unreachable           keep hits in dead/unreachable code
  * --co-occur              keep hits whose enclosing symbol matches
                            *every* -e pattern (cross-pattern correlation)
  * --missing-pattern P     drop hits whose enclosing symbol also matches P

Display:
  * --rank-by importance    sort by enclosing-symbol PageRank desc
  * --group-by symbol       collapse hits inside the same symbol

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because grep outputs are invocation-scoped annotated text match
enumerations — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.grep_helpers import (
    attach_blame,
    attach_heat,
    attach_pagerank,
    build_bridge_index,
    build_clone_index,
    build_interval_index,
    build_orphan_set,
    build_reachable_set,
    detect_engine,
    find_enclosing,
    group_by_symbol,
    indexed_file_scan,
    lookup_clone_siblings,
    run_search,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.index.file_roles import ROLE_SOURCE, ROLE_TEST, classify_file
from roam.output.formatter import abbrev_kind, json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Source-only exclusion patterns
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# W607-BV substrate-CALL boundaries (ADDITIVE to W607-G outer-guard)
# ---------------------------------------------------------------------------
# Module-level shims that delegate to the underlying grep substrate. Tests
# monkeypatch THESE shims (not the helper module) so the W607-BV marker
# plumbing inside ``grep_cmd`` can disclose substrate-CALL failures without
# colliding with the existing W607-G outer-guard markers
# (``grep_engine_pin_missing:`` / ``grep_engine_fanout_fallback:`` /
# ``grep_ripgrep_failed:`` / ``grep_git_grep_failed:`` /
# ``grep_engine_failed:`` / ``grep_indexed_scan_failed:``).
#
# Each shim accepts the same arguments as the underlying substrate call
# and returns the same result. A raise inside any shim becomes a
# ``grep_<phase>_failed:<exc_class>:<detail>`` marker via the
# ``_run_check_bv`` closure inside ``grep_cmd``.
#
# cmd_grep is the TEXT-CONTENT SEARCH sibling of cmd_search (W607-BR),
# cmd_search_semantic (W607-BO), and cmd_retrieve (W607-BI). Closes the
# 4-way DISCOVERABILITY LAYER with distinct marker prefixes:
#   * ``search_*``           cmd_search          (W607-BR, exact-match)
#   * ``search_semantic_*``  cmd_search_semantic (W607-BO, ANN-rank)
#   * ``retrieve_*``         cmd_retrieve        (W607-BI, graph-aware)
#   * ``grep_*``             cmd_grep            (W607-BV, text-content)
# A 4-way envelope inspection can demultiplex every consumer's substrate
# axis.


def _select_engine():
    """W607-BV substrate-CALL: choose the grep engine.

    Delegates to ``detect_engine()`` from ``grep_helpers``. A raise
    (env var corruption, ``shutil.which`` failure on a hostile PATH)
    surfaces a marker via ``grep_select_engine_failed:``; degraded
    default returns ``"fallback"`` so the indexed-scan path still
    runs and the envelope still emits.
    """
    return detect_engine()


def _compile_patterns(positional, patterns, patterns_from):
    """W607-BV substrate-CALL: resolve patterns from positional + ``-e`` + ``--patterns-from``.

    Composes the three pattern sources into one ordered list (positional
    first, then -e repeatable, then --patterns-from contents) with empty
    entries dropped. A raise (e.g. unreadable patterns-file, decoding
    failure) surfaces a marker via ``grep_compile_patterns_failed:``;
    degraded default returns ``[]`` so the empty-patterns branch fires
    a structured usage-error envelope.
    """
    pats: list[str] = []
    if positional:
        pats.append(positional)
    pats.extend(patterns)
    if patterns_from:
        pats.extend(_read_patterns_file(patterns_from))
    return [p for p in pats if p]


def _run_engine(*, patterns, root, globs, fixed_string, case_insensitive, word_boundary, engine):
    """W607-BV substrate-CALL: execute the grep engine.

    Delegates to ``run_search()`` from ``grep_helpers``. A raise here
    surfaces a marker via ``grep_run_engine_failed:``; the W607-G
    outer-guard's engine-specific markers (``grep_ripgrep_failed:`` /
    ``grep_git_grep_failed:`` / ``grep_engine_failed:``) remain emitted
    on the same call (the outer-guard try/except still wraps the shim
    call). Degraded default returns ``[]`` so the engine-fallback
    fan-out can still trigger downstream.
    """
    return run_search(
        patterns=patterns,
        root=root,
        globs=globs,
        fixed_string=fixed_string,
        case_insensitive=case_insensitive,
        word_boundary=word_boundary,
        engine=engine,
    )


def _apply_reachability_filter(conn, reachable_from):
    """W607-BV substrate-CALL: build the reachable-from set.

    Delegates to ``build_reachable_set()`` from ``grep_helpers``. A
    raise (corrupt edges table, OOM on adjacency build) surfaces a
    marker via ``grep_apply_reachability_filter_failed:``; degraded
    default returns ``None`` so the unresolved-entry branch fires a
    Pattern-1D structured envelope.
    """
    return build_reachable_set(conn, reachable_from)


def _apply_co_occur_filter(matches, patterns, fixed, case_insensitive):
    """W607-BV substrate-CALL: cross-pattern co-occurrence filter.

    A raise (regex compile error post-pattern-mutation) surfaces a
    marker via ``grep_apply_co_occur_filter_failed:``; degraded default
    returns the unfiltered matches so the user still gets results
    instead of a silent empty list.
    """
    return _filter_co_occur(matches, patterns, fixed, case_insensitive)


def _apply_missing_pattern(matches, missing_pattern, case_insensitive):
    """W607-BV substrate-CALL: anti-correlation filter.

    A raise surfaces a marker via ``grep_apply_missing_pattern_failed:``;
    degraded default returns the unfiltered matches.
    """
    return _filter_missing_pattern(matches, missing_pattern, case_insensitive)


def _apply_rank_by(matches, conn):
    """W607-BV substrate-CALL: PageRank annotation for ``--rank-by importance``.

    Delegates to ``attach_pagerank()`` from ``grep_helpers``. A raise
    (graph_metrics schema drift) surfaces a marker via
    ``grep_apply_rank_by_failed:``; degraded default leaves the matches
    untouched so the line-ordered sort still produces output.
    """
    attach_pagerank(matches, conn)


def _apply_group_by(matches):
    """W607-BV substrate-CALL: collapse hits inside the same symbol.

    Delegates to ``group_by_symbol()``. A raise surfaces a marker via
    ``grep_apply_group_by_failed:``; degraded default returns ``None``
    so the un-grouped text-output path runs.
    """
    return group_by_symbol(matches)


def _apply_blame_heat(matches, conn, root, *, with_blame, with_heat):
    """W607-BV substrate-CALL: blame + heat annotation.

    Wraps the two enrichment passes (``attach_heat`` + ``attach_blame``)
    so a raise inside either surfaces a marker via
    ``grep_apply_blame_heat_failed:``. Degraded default leaves the
    matches unannotated; the verdict still emits.
    """
    if with_heat:
        attach_heat(matches, conn)
    if with_blame:
        attach_blame(matches, root)


_SOURCE_ONLY_EXCLUDES = [
    "*.md",
    "*.markdown",
    "*.txt",
    "*.rst",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.ini",
    "*.cfg",
    "*.lock",
    "*.example",
    "*.sample",
    "*.svg",
    "*.png",
    "*.jpg",
    "*.gif",
    "*.ico",
    "docs/**",
    "**/docs/**",
]


def _matches_any_exclude(path, excludes):
    from roam.index.gitignore import matches_gitignore

    for pat in excludes:
        if matches_gitignore(path, pat):
            return True
    return False


def _normalise_glob(g: str) -> str:
    """Normalise shorthand: 'ts' / '.ts' → '*.ts'. Leaves explicit globs alone."""
    if "*" in g or "?" in g or "/" in g:
        return g
    ext = g if g.startswith(".") else f".{g}"
    return f"*{ext}"


def _read_patterns_file(p: Path) -> list[str]:
    """Read patterns one-per-line, ignoring blanks and ``#`` comments."""
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="grep",
    category="exploration",
    summary="Context-enriched grep with reachability, clone, and bridge annotations",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("grep")
@click.argument("positional", required=False)
@click.option(
    "-e",
    "--regex",
    "patterns",
    multiple=True,
    help="Pattern (repeatable). Treated as alternation across patterns.",
)
@click.option(
    "--patterns-from",
    "patterns_from",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read patterns from <FILE> (one per line; '#' for comments).",
)
@click.option(
    "-g",
    "--glob",
    "globs",
    multiple=True,
    help="Glob filter (repeatable, e.g. '-g py -g md'). Shorthand 'ts'/'.ts' → '*.ts'.",
)
@click.option("-F", "--fixed-string", "fixed", is_flag=True, help="Literal mode (no regex).")
@click.option("-i", "--ignore-case", "case_insensitive", is_flag=True, help="Case-insensitive search.")
@click.option("-w", "--word", "word_boundary", is_flag=True, help="Match whole words only.")
@click.option("-n", "count", default=50, help="Max results to show.")
@click.option("-s", "--source-only", is_flag=True, help="Exclude docs, configs, and non-source files.")
@click.option("-t", "--test-only", is_flag=True, help="Only search in test files.")
@click.option("--exclude", "exclude_patterns", default=None, help="Comma-separated exclusion globs.")
@click.option("--reachable-from", "reachable_from", default=None, help="Keep only hits reachable from <entry>.")
@click.option("--unreachable", is_flag=True, help="Keep only hits in unreachable / orphan code.")
@click.option("--co-occur", is_flag=True, help="Keep hits whose enclosing symbol matches every -e pattern.")
@click.option(
    "--missing-pattern",
    "missing_pattern",
    default=None,
    help="Drop hits whose enclosing symbol also matches this pattern.",
)
@click.option("--rank-by", "rank_by", type=click.Choice(["line", "importance"]), default="line")
@click.option("--group-by", "group_by", type=click.Choice(["none", "symbol"]), default="none")
@click.option("--blame", "with_blame", is_flag=True, help="Annotate hits with last-modified author + date.")
@click.option("--heat", "with_heat", is_flag=True, help="Annotate hits with churn / commit count of file.")
@click.option("--no-clones", is_flag=True, help="Skip clone-class annotation.")
@click.option("--no-bridges", is_flag=True, help="Skip bridge annotation.")
@click.pass_context
def grep_cmd(
    ctx,
    positional,
    patterns,
    patterns_from,
    globs,
    fixed,
    case_insensitive,
    word_boundary,
    count,
    source_only,
    test_only,
    exclude_patterns,
    reachable_from,
    unreachable,
    co_occur,
    missing_pattern,
    rank_by,
    group_by,
    with_blame,
    with_heat,
    no_clones,
    no_bridges,
):
    """Context-enriched grep with reachability, clone, and bridge annotations.

    Examples:

      \b
      roam grep "TODO"                       # single pattern
      roam grep -e foo -e bar                # multi-pattern alternation
      roam grep --patterns-from todo.txt
      roam grep -e auth -e cookie --co-occur # both must hit same symbol
      roam grep -e auth_check --missing-pattern is_admin
      roam grep "DATABASE_URL" --reachable-from main
      roam grep "deprecated" --unreachable   # only in dead code
      roam grep "format_name" --rank-by importance --group-by symbol

    See also ``search`` (FTS5 symbol search), ``refs-text`` (literal-string
    audit with verdict), and ``history-grep`` (through-history pickaxe).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    # W607-BV: ADDITIVE per-phase substrate-CALL marker plumbing on top of
    # the W607-G outer-guard pipeline below. cmd_grep is the TEXT-CONTENT
    # SEARCH sibling of cmd_search (W607-BR), cmd_search_semantic (W607-BO),
    # and cmd_retrieve (W607-BI). A silent failure in any of its substrate
    # boundaries (engine select, pattern compile, engine run, reachability
    # filter, co-occur filter, missing-pattern filter, rank-by, group-by,
    # blame/heat enrichment, serialize) directly degrades agent productivity.
    # W607-BV wraps each substrate call so a raise becomes a structured
    # ``grep_<phase>_failed:<exc_class>:<detail>`` marker instead of a Click
    # traceback. The W607-G outer-guard markers
    # (``grep_engine_pin_missing:`` / ``grep_engine_fanout_fallback:`` /
    # ``grep_ripgrep_failed:`` / ``grep_git_grep_failed:`` /
    # ``grep_engine_failed:`` / ``grep_indexed_scan_failed:``) remain as
    # final safety nets. Empty W607-BV bucket -> byte-identical envelope
    # (hash-stable).
    _w607bv_warnings_out: list[str] = []

    def _run_check_bv(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BV marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``grep_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bv_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bv_warnings_out.append(f"grep_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # --- Resolve patterns from positional + -e + --patterns-from ---
    # W607-BV compile_patterns substrate-CALL: a raise surfaces
    # ``grep_compile_patterns_failed:``; degraded default returns ``[]`` so
    # the empty-patterns branch fires the existing structured usage-error
    # envelope.
    pats = _run_check_bv(
        "compile_patterns",
        _compile_patterns,
        positional,
        patterns,
        patterns_from,
        default=[],
    )
    if not pats:
        # Pattern 1B/1C discipline: emit a structured envelope in JSON mode
        # so MCP wrappers see actionable state, not a raw COMMAND_FAILED.
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "grep",
                        budget=token_budget,
                        summary={
                            "verdict": "no patterns provided",
                            "state": "usage_error",
                            "partial_success": True,
                            "total": 0,
                        },
                        hint="Pass a positional pattern, -e/--regex, or --patterns-from FILE.",
                        matches=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: no patterns provided")
            click.echo("Pass a positional pattern, -e/--regex, or --patterns-from FILE.")
        raise SystemExit(2)

    if co_occur and len(pats) < 2:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "grep",
                        budget=token_budget,
                        summary={
                            "verdict": "--co-occur requires at least two -e patterns",
                            "state": "usage_error",
                            "partial_success": True,
                            "total": 0,
                        },
                        hint="Pass two or more patterns via repeated -e flags.",
                        matches=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: --co-occur requires at least two -e patterns")
        raise SystemExit(2)

    ensure_index()
    root = find_project_root()
    glob_filter = [_normalise_glob(g) for g in globs] if globs else []

    # W607-G: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the SUBPROCESS-shaped engine fan-out. cmd_grep does
    # NOT call any W605-plumbed substrate (search_fts / fts5_available /
    # tfidf_populated): its substrate is the ripgrep / git-grep / indexed
    # scan subprocess axis. Per CLAUDE.md: "ripgrep > git grep > fallback
    # (pin via `ROAM_GREP_ENGINE`)". Three silent fallback locations:
    #   1. ``detect_engine`` silently returns ``"fallback"`` when
    #      ROAM_GREP_ENGINE pins an absent binary → user pin not honored.
    #   2. ``run_search`` / ``_run_and_parse`` silently swallow
    #      FileNotFoundError + TimeoutExpired → return [] (looks like a
    #      no-match) while the subprocess never ran.
    #   3. ``indexed_file_scan`` silently OSError-skips unreadable files.
    # Marker family is ``grep_*`` (NOT ``search_*`` / ``complete_*`` /
    # ``semantic_*`` / ``history_*``) — cmd_grep is the subprocess axis,
    # distinct from the lexical-search trio (search / complete /
    # search_semantic) sealed at W607-E/F/A and from the through-history
    # pickaxe (history-grep) reserved for W607-H. Empty bucket →
    # byte-identical envelope (hash-stable). Non-empty bucket →
    # summary.warnings_out + summary.partial_success=True + top-level
    # mirror.
    warnings_out: list[str] = []

    # --- Engine pin honoring check (W607-G outer-guard) ---
    # If the user pinned ROAM_GREP_ENGINE to a specific binary AND the
    # binary is not on PATH, ``detect_engine`` silently returns
    # ``"fallback"``. That's an unhonored pin — disclose it.
    _engine_pin = os.environ.get("ROAM_GREP_ENGINE", "auto").strip().lower()
    # W607-BV select_engine substrate-CALL: a raise surfaces
    # ``grep_select_engine_failed:``; degraded default returns ``"fallback"``
    # so the indexed-scan path still runs and the envelope still emits.
    engine = _run_check_bv("select_engine", _select_engine, default="fallback")
    if _engine_pin in {"ripgrep", "rg"} and engine != "ripgrep":
        warnings_out.append("grep_engine_pin_missing:ripgrep:binary 'rg' not on PATH (shutil.which returned None)")
    elif _engine_pin in {"git", "git-grep"} and engine != "git":
        warnings_out.append("grep_engine_pin_missing:git:binary 'git' not on PATH (shutil.which returned None)")

    # --- Run engine (outer-guarded) ---
    # W607-BV run_engine substrate-CALL (ADDITIVE to W607-G outer-guard).
    # The same call is now routed through ``_run_engine`` so tests can
    # monkeypatch the module-level shim to simulate a substrate failure.
    # On raise: W607-BV surfaces ``grep_run_engine_failed:`` AND the W607-G
    # outer-guard preserves its engine-specific marker
    # (``grep_ripgrep_failed:`` / ``grep_git_grep_failed:`` /
    # ``grep_engine_failed:``). Degraded default returns ``[]`` so the
    # engine-fallback fan-out can still trigger.
    try:
        matches = _run_check_bv(
            "run_engine",
            _run_engine,
            patterns=pats,
            root=root,
            globs=glob_filter,
            fixed_string=fixed,
            case_insensitive=case_insensitive,
            word_boundary=word_boundary,
            engine=engine,
            default=None,
        )
        if matches is None:
            # The shim raised AND W607-BV captured it; mirror to the
            # W607-G outer-guard family below so the existing markers
            # also fire.
            _captured = next(
                (m for m in _w607bv_warnings_out if m.startswith("grep_run_engine_failed:")),
                None,
            )
            if _captured is not None:
                if engine == "ripgrep":
                    warnings_out.append(_captured.replace("grep_run_engine_failed:", "grep_ripgrep_failed:", 1))
                elif engine == "git":
                    warnings_out.append(_captured.replace("grep_run_engine_failed:", "grep_git_grep_failed:", 1))
                else:
                    warnings_out.append(_captured.replace("grep_run_engine_failed:", "grep_engine_failed:", 1))
            matches = []
    except Exception as exc:  # noqa: BLE001 — W607-G outer-guard
        if engine == "ripgrep":
            warnings_out.append(f"grep_ripgrep_failed:{type(exc).__name__}:{exc}")
        elif engine == "git":
            warnings_out.append(f"grep_git_grep_failed:{type(exc).__name__}:{exc}")
        else:
            warnings_out.append(f"grep_engine_failed:{type(exc).__name__}:{exc}")
        matches = []
    used_engine = engine

    # Engine fallback to indexed scan.
    # W1010 lineage: when ``detect_engine`` returns ``"fallback"`` (no rg/git
    # on PATH) AND the indexed scan actually runs, relabel ``used_engine`` to
    # ``"indexed_scan"`` so the envelope discloses which engine produced the
    # results. The pre-relabel string ``"fallback"`` claimed "no engine ran"
    # while the indexed_file_scan code path was, in fact, the engine — that
    # is exactly the silent-fallback shape CP45/CP46 warn against.
    if not matches and engine == "fallback":
        # W607-G: disclose the auto-fan-out fallthrough so the agent can
        # distinguish "no engines on PATH (fell through to indexed scan)"
        # from "engines present, just no matches". This is the
        # silent-fallback shape per Pattern-2: it changes behaviour
        # (different engine produces the results) without telling the
        # caller.
        _rg_present = bool(shutil.which("rg"))
        _git_present = bool(shutil.which("git"))
        if not _rg_present and not _git_present:
            warnings_out.append("grep_engine_fanout_fallback:auto:neither 'rg' nor 'git' on PATH")
        flags = re.IGNORECASE if case_insensitive else 0
        if fixed:
            compiled = [re.compile(re.escape(p), flags) for p in pats]
        else:
            compiled = [re.compile(p, flags) for p in pats]
        try:
            with open_db(readonly=True) as conn_tmp:
                matches = indexed_file_scan(compiled, conn_tmp, root, glob_filter)
        except Exception as exc:  # noqa: BLE001 — W607-G outer-guard
            warnings_out.append(f"grep_indexed_scan_failed:{type(exc).__name__}:{exc}")
            matches = []
        used_engine = "indexed_scan"

    # --- Apply path-based filters ---
    excludes = []
    if source_only:
        excludes.extend(_SOURCE_ONLY_EXCLUDES)
    if exclude_patterns:
        excludes.extend(p.strip() for p in exclude_patterns.split(",") if p.strip())
    if excludes:
        matches = [m for m in matches if not _matches_any_exclude(m["path"], excludes)]
    if source_only:
        matches = [m for m in matches if classify_file(m["path"]) in (ROLE_SOURCE, ROLE_TEST)]
    if test_only:
        matches = [m for m in matches if is_test_file(m["path"])]

    # --- Index-aware enrichment ---
    if not matches:
        # W607-G + W607-BV combined disclosure: merge both buckets so
        # consumers reading either channel see the full lineage. Empty
        # combined bucket -> byte-identical envelope (hash-stable).
        _combined_empty = list(warnings_out) + list(_w607bv_warnings_out)
        _emit_empty(
            json_mode,
            pats,
            token_budget,
            used_engine,
            warnings_out=_combined_empty,
        )
        return

    with open_db(readonly=True) as conn:
        match_paths = {m["path"] for m in matches}
        interval_idx = build_interval_index(conn, match_paths)

        # Bulk attach enclosing symbol (replaces N+1)
        for m in matches:
            sym = find_enclosing(interval_idx, m["path"], m["line"])
            m["_enclosing"] = sym
            m["enclosing_symbol"] = sym["qualified_name"] if sym else None
            m["enclosing_kind"] = sym["kind"] if sym else None

        # --- Reachability filter / badge ---
        reach_set: set[int] | None = None
        unreachable_filter_active = False
        if reachable_from:
            # W607-BV apply_reachability_filter substrate-CALL: a raise
            # surfaces ``grep_apply_reachability_filter_failed:``; degraded
            # default returns None so the unresolved-entry branch fires
            # the Pattern-1D structured envelope.
            reach_set = _run_check_bv(
                "apply_reachability_filter",
                _apply_reachability_filter,
                conn,
                reachable_from,
                default=None,
            )
            if reach_set is None:
                # Pattern 1B/1D: degraded resolution — anchor symbol not in
                # index. Emit a structured envelope so MCP wrappers see
                # actionable state instead of a raw COMMAND_FAILED.
                msg = f"entry symbol '{reachable_from}' not found in index"
                if json_mode:
                    click.echo(
                        to_json(
                            json_envelope(
                                "grep",
                                budget=token_budget,
                                summary={
                                    "verdict": msg,
                                    "state": "unresolved_entry",
                                    "partial_success": True,
                                    "resolution": "unresolved",
                                    "total": 0,
                                },
                                hint="Verify the symbol exists; try `roam search <name>` first.",
                                reachable_from=reachable_from,
                                matches=[],
                            )
                        )
                    )
                else:
                    click.echo(f"VERDICT: {msg}")
                raise SystemExit(1)
        if reach_set is not None:
            for m in matches:
                sym = m["_enclosing"]
                m["reachable"] = bool(sym and sym["id"] in reach_set)
        if unreachable:
            unreachable_filter_active = True
            if reach_set is not None:
                matches = [m for m in matches if not m.get("reachable", False)]
            else:
                orphans = build_orphan_set(conn)
                kept = []
                for m in matches:
                    sym = m["_enclosing"]
                    is_orph = (sym is None) or (sym["id"] in orphans)
                    m["reachable"] = not is_orph
                    if is_orph:
                        kept.append(m)
                matches = kept
        elif reachable_from:
            matches = [m for m in matches if m.get("reachable", False)]

        # --- Co-occurrence filter ---
        if co_occur:
            # W607-BV apply_co_occur_filter substrate-CALL: a raise surfaces
            # ``grep_apply_co_occur_filter_failed:``; degraded default
            # returns the unfiltered matches so the user still gets results.
            matches = _run_check_bv(
                "apply_co_occur_filter",
                _apply_co_occur_filter,
                matches,
                pats,
                fixed,
                case_insensitive,
                default=matches,
            )

        # --- Anti-correlation filter ---
        if missing_pattern:
            # W607-BV apply_missing_pattern substrate-CALL: a raise surfaces
            # ``grep_apply_missing_pattern_failed:``; degraded default
            # returns the unfiltered matches.
            matches = _run_check_bv(
                "apply_missing_pattern",
                _apply_missing_pattern,
                matches,
                missing_pattern,
                case_insensitive,
                default=matches,
            )

        if not matches:
            # W607-G + W607-BV combined disclosure on the post-filter
            # empty branch.
            _combined_filtered = list(warnings_out) + list(_w607bv_warnings_out)
            _emit_empty(
                json_mode,
                pats,
                token_budget,
                used_engine,
                filtered=True,
                warnings_out=_combined_filtered,
            )
            return

        # --- Annotations ---
        if rank_by == "importance":
            # W607-BV apply_rank_by substrate-CALL: a raise surfaces
            # ``grep_apply_rank_by_failed:``; degraded default leaves the
            # matches untouched so the line-ordered sort still produces
            # output.
            _run_check_bv(
                "apply_rank_by",
                _apply_rank_by,
                matches,
                conn,
                default=None,
            )
        # W607-BV apply_blame_heat substrate-CALL: a raise inside either
        # ``attach_heat`` or ``attach_blame`` surfaces
        # ``grep_apply_blame_heat_failed:``. Degraded default leaves
        # matches unannotated.
        if with_heat or with_blame:
            _run_check_bv(
                "apply_blame_heat",
                _apply_blame_heat,
                matches,
                conn,
                root,
                with_blame=with_blame,
                with_heat=with_heat,
                default=None,
            )

        clone_idx: dict = {} if no_clones else build_clone_index(conn)
        bridge_idx: dict = {} if no_bridges else build_bridge_index(conn)

        for m in matches:
            sym = m["_enclosing"]
            if clone_idx and sym:
                sibs = lookup_clone_siblings(clone_idx, sym, m["path"])
                if sibs:
                    m["clone_siblings"] = sibs
            if bridge_idx:
                bridges = bridge_idx.get(m["path"])
                if bridges:
                    m["bridge_links"] = bridges

        # --- Sort ---
        if rank_by == "importance":
            matches.sort(key=lambda m: (-(m.get("pagerank") or 0.0), m["path"], m["line"]))
        else:
            matches.sort(key=lambda m: (m["path"], m["line"]))

        # --- Group ---
        # W607-BV apply_group_by substrate-CALL: a raise surfaces
        # ``grep_apply_group_by_failed:``; degraded default returns None
        # so the un-grouped text-output path runs.
        groups = (
            _run_check_bv("apply_group_by", _apply_group_by, matches, default=None) if group_by == "symbol" else None
        )

    # --- Output ---
    unique_files = len({m["path"] for m in matches})
    verdict = f"{len(matches)} matches in {unique_files} files for {_pat_label(pats)}"
    if reachable_from:
        verdict += f" — reachable from {reachable_from}"
    if unreachable_filter_active:
        verdict += " — restricted to unreachable code"

    if json_mode:
        # W607-G + W607-BV combined disclosure on the happy match path.
        # W607-BV serialize_envelope substrate-CALL is enforced inside
        # _emit_json by wrapping the to_json call via the _run_check_bv
        # closure (passed-through via the bucket so a serialize raise
        # falls back to a minimal envelope rather than crashing).
        _combined_match = list(warnings_out) + list(_w607bv_warnings_out)
        _emit_json(
            matches=matches,
            groups=groups,
            count=count,
            patterns=pats,
            verdict=verdict,
            token_budget=token_budget,
            engine=used_engine,
            source_only=source_only,
            test_only=test_only,
            excludes=excludes,
            reachable_from=reachable_from,
            unreachable=unreachable_filter_active,
            rank_by=rank_by,
            group_by=group_by,
            warnings_out=_combined_match,
            _run_check_bv=_run_check_bv,
        )
        return

    _emit_text(matches, groups, count, verdict, group_by)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _compile(pat: str, fixed: bool, ci: bool) -> re.Pattern:
    flags = re.IGNORECASE if ci else 0
    return re.compile(re.escape(pat) if fixed else pat, flags)


def _filter_co_occur(matches, patterns, fixed, ci):
    """Drop hits whose enclosing symbol is not also matched by every other pattern.

    'Matched' here is checked by re-scanning lines within the symbol's span
    against the surviving content already loaded — but matches only know
    their own line. The cheap correct way: bucket matches by (path, sym),
    check that the bucket's set of patterns covers every input pattern.
    """
    if not matches:
        return matches
    compiled = [_compile(p, fixed, ci) for p in patterns]

    # Bucket by (path, enclosing_id-or-None)
    buckets: dict[tuple, list[dict]] = {}
    for m in matches:
        sym = m.get("_enclosing")
        key = (m["path"], sym["id"] if sym else None)
        buckets.setdefault(key, []).append(m)

    out: list[dict] = []
    for key, bucket in buckets.items():
        if key[1] is None:
            continue  # cannot co-occur within a symbol that doesn't exist
        covered = set()
        for m in bucket:
            for i, rx in enumerate(compiled):
                if rx.search(m["content"]):
                    covered.add(i)
        if len(covered) == len(compiled):
            out.extend(bucket)
    return out


def _filter_missing_pattern(matches, pattern, ci):
    """Drop hits whose enclosing symbol's lines also match ``pattern``.

    Implementation note: we lazily read each enclosing-symbol's source
    span only once per (path, sym_id) and cache the boolean.
    """
    if not matches:
        return matches
    rx = re.compile(pattern, re.IGNORECASE if ci else 0)
    cache: dict[tuple, bool] = {}
    root = find_project_root()
    out: list[dict] = []
    for m in matches:
        sym = m.get("_enclosing")
        if not sym:
            out.append(m)
            continue
        key = (m["path"], sym["id"])
        if key not in cache:
            cache[key] = _span_matches(root, m["path"], sym["line_start"], sym["line_end"], rx)
        if not cache[key]:
            out.append(m)
    return out


def _span_matches(root: Path, path: str, line_start: int, line_end: int, rx: re.Pattern) -> bool:
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = text.splitlines()
    snippet = "\n".join(lines[max(0, line_start - 1) : line_end])
    return bool(rx.search(snippet))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _pat_label(pats: list[str]) -> str:
    if len(pats) == 1:
        return f"'{pats[0]}'"
    return f"{len(pats)} patterns"


def _emit_empty(json_mode, patterns, budget, engine, filtered=False, *, warnings_out=None):
    label = _pat_label(patterns)
    suffix = " after filters" if filtered else ""
    if json_mode:
        _summary: dict = {
            "verdict": f"no matches for {label}{suffix}",
            "total": 0,
            "engine": engine,
        }
        # W607-G: non-empty bucket → summary mirror + partial_success flip
        # + top-level mirror. Empty bucket → byte-identical envelope.
        if warnings_out:
            _summary["warnings_out"] = list(warnings_out)
            _summary["partial_success"] = True
        extra: dict = {}
        if warnings_out:
            extra["warnings_out"] = list(warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "grep",
                    budget=budget,
                    summary=_summary,
                    patterns=list(patterns),
                    matches=[],
                    **extra,
                )
            )
        )
    else:
        click.echo(f"VERDICT: no matches for {label}{suffix}")
        click.echo()
        click.echo(f"No matches for {label}{suffix}.")


def _emit_json(
    *,
    matches,
    groups,
    count,
    patterns,
    verdict,
    token_budget,
    engine,
    source_only,
    test_only,
    excludes,
    reachable_from,
    unreachable,
    rank_by,
    group_by,
    warnings_out=None,
    _run_check_bv=None,
):
    serialised = []
    for m in matches[:count]:
        entry = {"path": m["path"], "line": m["line"], "content": m["content"]}
        for k in (
            "enclosing_symbol",
            "enclosing_kind",
            "reachable",
            "pagerank",
            "heat_churn",
            "heat_commits",
            "blame_author",
            "blame_date",
            "clone_siblings",
            "bridge_links",
        ):
            if m.get(k) is not None:
                entry[k] = m[k]
        serialised.append(entry)

    payload = {
        "patterns": list(patterns),
        "engine": engine,
        "total": len(matches),
        "source_only": source_only,
        "test_only": test_only,
        "exclude_patterns": excludes if excludes else None,
        "reachable_from": reachable_from,
        "unreachable": unreachable,
        "rank_by": rank_by,
        "group_by": group_by,
        "matches": serialised,
    }
    if groups is not None:
        payload["groups"] = [_serialise_group(g) for g in groups[:count]]

    _summary: dict = {
        "verdict": verdict,
        "total": len(matches),
        "shown": len(serialised),
        "engine": engine,
    }
    # W607-G + W607-BV: non-empty combined bucket -> summary mirror +
    # partial_success flip + top-level mirror. Empty bucket ->
    # byte-identical envelope.
    extra: dict = {}
    if warnings_out:
        _summary["warnings_out"] = list(warnings_out)
        _summary["partial_success"] = True
        extra["warnings_out"] = list(warnings_out)
    _envelope = json_envelope(
        "grep",
        budget=token_budget,
        summary=_summary,
        **payload,
        **extra,
    )
    # W607-BV serialize_envelope substrate-CALL: wrap to_json so a
    # serialize raise falls back to a minimal envelope rather than
    # crashing the entire grep call.
    if _run_check_bv is not None:
        _text = _run_check_bv(
            "serialize_envelope",
            lambda env=_envelope: to_json(env),
            default=None,
        )
        if _text is None:
            _text = to_json(
                json_envelope(
                    "grep",
                    budget=token_budget,
                    summary={
                        "verdict": "grep serialize failed",
                        "warnings_out": list(warnings_out or []),
                        "partial_success": True,
                    },
                    warnings_out=list(warnings_out or []),
                )
            )
        click.echo(_text)
    else:
        click.echo(to_json(_envelope))


def _serialise_group(g):
    return {
        "path": g["path"],
        "enclosing_symbol": g["enclosing_symbol"],
        "enclosing_kind": g["enclosing_kind"],
        "count": g["count"],
        "first_line": g["first_line"],
        "samples": g["samples"],
        **{
            k: g[k]
            for k in (
                "pagerank",
                "heat_churn",
                "heat_commits",
                "blame_author",
                "blame_date",
                "reachable",
                "clone_siblings",
            )
            if k in g
        },
    }


def _emit_text(matches, groups, count, verdict, group_by):
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    if group_by == "symbol" and groups is not None:
        _emit_text_grouped(groups, count)
        return
    click.echo(f"=== {len(matches)} matches ===\n")
    shown = 0
    for m in matches:
        if shown >= count:
            click.echo(f"\n(+{len(matches) - count} more)")
            break
        sym = m.get("_enclosing")
        location = loc(m["path"], m["line"])
        sym_info = f"  in {abbrev_kind(sym['kind'])} {sym['qualified_name']}" if sym else ""
        suffix = _annotations_suffix(m)
        content = m["content"]
        if len(content) > 100:
            content = content[:97] + "..."
        click.echo(f"  {location}{sym_info}{suffix}")
        click.echo(f"    {content}")
        shown += 1


def _emit_text_grouped(groups, count):
    click.echo(f"=== {len(groups)} groups ===\n")
    shown = 0
    for g in groups:
        if shown >= count:
            click.echo(f"\n(+{len(groups) - count} more groups)")
            break
        sym = g["enclosing_symbol"] or "(top-level)"
        kind = abbrev_kind(g["enclosing_kind"]) if g["enclosing_kind"] else ""
        location = loc(g["path"], g["first_line"])
        suffix = _annotations_suffix(g)
        click.echo(f"  {location}  in {kind} {sym}  ({g['count']} hits){suffix}")
        for s in g["samples"][:2]:
            content = s["content"]
            if len(content) > 100:
                content = content[:97] + "..."
            click.echo(f"    L{s['line']}: {content}")
        shown += 1


def _annotations_suffix(m: dict) -> str:
    parts = []
    if "reachable" in m:
        parts.append("reachable" if m["reachable"] else "unreachable")
    if "pagerank" in m and m["pagerank"]:
        parts.append(f"pr={m['pagerank']:.4f}")
    if "heat_churn" in m and m["heat_churn"]:
        parts.append(f"churn={m['heat_churn']}")
    if "blame_author" in m and m["blame_author"]:
        parts.append(f"by {m['blame_author']}")
    if "clone_siblings" in m:
        parts.append(f"clones={len(m['clone_siblings'])}")
    if "bridge_links" in m:
        parts.append(f"bridges={len(m['bridge_links'])}")
    return f"  [{' '.join(parts)}]" if parts else ""
