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

import re
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

    # --- Resolve patterns from positional + -e + --patterns-from ---
    pats: list[str] = []
    if positional:
        pats.append(positional)
    pats.extend(patterns)
    if patterns_from:
        pats.extend(_read_patterns_file(patterns_from))
    pats = [p for p in pats if p]
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

    # --- Run engine ---
    engine = detect_engine()
    matches = run_search(
        patterns=pats,
        root=root,
        globs=glob_filter,
        fixed_string=fixed,
        case_insensitive=case_insensitive,
        word_boundary=word_boundary,
        engine=engine,
    )
    used_engine = engine

    # Engine fallback to indexed scan.
    # W1010 lineage: when ``detect_engine`` returns ``"fallback"`` (no rg/git
    # on PATH) AND the indexed scan actually runs, relabel ``used_engine`` to
    # ``"indexed_scan"`` so the envelope discloses which engine produced the
    # results. The pre-relabel string ``"fallback"`` claimed "no engine ran"
    # while the indexed_file_scan code path was, in fact, the engine — that
    # is exactly the silent-fallback shape CP45/CP46 warn against.
    if not matches and engine == "fallback":
        flags = re.IGNORECASE if case_insensitive else 0
        if fixed:
            compiled = [re.compile(re.escape(p), flags) for p in pats]
        else:
            compiled = [re.compile(p, flags) for p in pats]
        with open_db(readonly=True) as conn_tmp:
            matches = indexed_file_scan(compiled, conn_tmp, root, glob_filter)
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
        _emit_empty(json_mode, pats, token_budget, used_engine)
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
            reach_set = build_reachable_set(conn, reachable_from)
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
            matches = _filter_co_occur(matches, pats, fixed, case_insensitive)

        # --- Anti-correlation filter ---
        if missing_pattern:
            matches = _filter_missing_pattern(matches, missing_pattern, case_insensitive)

        if not matches:
            _emit_empty(json_mode, pats, token_budget, used_engine, filtered=True)
            return

        # --- Annotations ---
        if rank_by == "importance":
            attach_pagerank(matches, conn)
        if with_heat:
            attach_heat(matches, conn)
        if with_blame:
            attach_blame(matches, root)

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
        groups = group_by_symbol(matches) if group_by == "symbol" else None

    # --- Output ---
    unique_files = len({m["path"] for m in matches})
    verdict = f"{len(matches)} matches in {unique_files} files for {_pat_label(pats)}"
    if reachable_from:
        verdict += f" — reachable from {reachable_from}"
    if unreachable_filter_active:
        verdict += " — restricted to unreachable code"

    if json_mode:
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


def _emit_empty(json_mode, patterns, budget, engine, filtered=False):
    label = _pat_label(patterns)
    suffix = " after filters" if filtered else ""
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "grep",
                    budget=budget,
                    summary={"verdict": f"no matches for {label}{suffix}", "total": 0, "engine": engine},
                    patterns=list(patterns),
                    matches=[],
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

    click.echo(
        to_json(
            json_envelope(
                "grep",
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "total": len(matches),
                    "shown": len(serialised),
                    "engine": engine,
                },
                **payload,
            )
        )
    )


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
