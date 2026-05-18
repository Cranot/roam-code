"""roam refs-text — audit verdict for a literal string across the project.

Different shape from ``roam grep``:

* grep prints lines, lets you eyeball the result.
* refs-text *answers a question*: "is this string still load-bearing?"

Given one or more strings (typically file paths, config keys, error
messages, route patterns, or identifiers), it groups every reference by
*surface* (code, test, docs, config, generated, vendored), annotates
reachability for code hits, and emits a per-string verdict:

  * SAFE-TO-REMOVE    — only doc / test / dead-code references
  * REVIEW            — referenced in one or two reachable code symbols
  * LOAD-BEARING      — referenced in many reachable code symbols, or
                        in symbols with non-trivial PageRank

Reuses ``grep_helpers`` so reachability / clone / bridge logic stay
single-sourced.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because refs-text outputs are invocation-scoped per-string
verdict envelopes (SAFE-TO-REMOVE / REVIEW / LOAD-BEARING) — not
per-location violations. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os
import shutil

import click

from roam.capability import roam_capability
from roam.commands.grep_helpers import (
    build_bridge_index,
    build_clone_index,
    build_interval_index,
    build_orphan_set,
    build_reachable_set,
    classify_surface,
    detect_engine,
    find_enclosing,
    indexed_file_scan,
    lookup_clone_siblings,
    run_search,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.evidence._vocabulary import REFERENCE_REMOVAL_VERDICTS
from roam.output.formatter import json_envelope, loc, to_json

# ---------------------------------------------------------------------------
# Verdict thresholds
# ---------------------------------------------------------------------------

_PR_HOT_THRESHOLD = 0.0005  # PageRank above this counts as "hot"
_REVIEW_REACHABLE_MAX = 2  # ≤ this → REVIEW; more → LOAD-BEARING


def _validate_verdict(verdict: str) -> str:
    """Assert ``verdict`` is in the W1156 closed-enum vocabulary.

    Display form is UPPERCASE-WITH-HYPHENS; canonical form is
    lowercase+underscore. Normalize before membership check and return
    the original display form so callers stay unchanged.
    """
    canonical = verdict.lower().replace("-", "_")
    assert canonical in REFERENCE_REMOVAL_VERDICTS, (
        f"verdict {verdict!r} (canonical {canonical!r}) not in REFERENCE_REMOVAL_VERDICTS - see W1156"
    )
    return verdict


def _verdict_for(per_string: dict) -> tuple[str, str]:
    """Return (verdict, reason) given a per-string analysis dict."""
    code = per_string["surfaces"].get("code", [])
    reachable = [m for m in code if m.get("reachable", True)]
    hot = [m for m in reachable if (m.get("pagerank") or 0.0) >= _PR_HOT_THRESHOLD]

    if not code:
        return _validate_verdict("SAFE-TO-REMOVE"), "no references in source code"
    if not reachable:
        return _validate_verdict("SAFE-TO-REMOVE"), f"{len(code)} code reference(s), none reachable"
    if hot:
        return _validate_verdict("LOAD-BEARING"), f"{len(reachable)} reachable, {len(hot)} in hot symbols"
    if len(reachable) <= _REVIEW_REACHABLE_MAX:
        names = ", ".join(sorted({m.get("enclosing_symbol") or m["path"] for m in reachable})[:3])
        return _validate_verdict("REVIEW"), f"{len(reachable)} reachable: {names}"
    return _validate_verdict("LOAD-BEARING"), f"{len(reachable)} reachable code references"


def _classify_match(m: dict, reach_set: set[int] | None, orphans: set[int]) -> str:
    """Map a match to a surface label, escalating dead code from 'code' to 'dead'.

    A match with no enclosing symbol (top-level statement, comment,
    import, decorator) stays in the file's surface — module-level
    statements run at import time and are not "dead".
    """
    base = classify_surface(m["path"])
    if base != "code":
        return base
    sym = m.get("_enclosing")
    if sym is None:
        return "code"
    if reach_set is not None:
        return "code" if sym["id"] in reach_set else "dead"
    return "dead" if sym["id"] in orphans else "code"


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="refs-text",
    category="exploration",
    summary="Audit literal strings across the project: per-surface refs + verdict",
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
@click.command("refs-text")
@click.argument("strings", nargs=-1)
@click.option("-e", "--regex", "extra", multiple=True, help="Additional string (repeatable). Same as positional args.")
@click.option(
    "--reachable-from",
    "reachable_from",
    default=None,
    help="Treat reachability as 'reachable from <entry>'. When omitted, dead = no inbound edges.",
)
@click.option("-g", "--glob", "globs", multiple=True, help="Restrict scan (repeatable; e.g. -g py -g md).")
@click.option("-F", "--fixed-string", "fixed", is_flag=True, default=True, help="Literal mode (default).")
@click.option(
    "-E",
    "--regexp",
    "regexp_mode",
    is_flag=True,
    default=False,
    help="W421 — regex mode: treat each string as a regex (ripgrep without -F). Slower on large repos.",
)
@click.option("-i", "--ignore-case", "ci", is_flag=True, help="Case-insensitive search.")
@click.option(
    "--with-clones/--no-clones",
    "with_clones",
    default=True,
    help="Annotate code hits with clone-class siblings.",
)
@click.option(
    "--with-bridges/--no-bridges",
    "with_bridges",
    default=True,
    help="Annotate config/template hits with cross-language bridge links.",
)
@click.option(
    "--per-match-detail",
    is_flag=True,
    help="Include every match in JSON output (default: only summary + per-surface counts).",
)
@click.pass_context
def refs_text_cmd(
    ctx, strings, extra, reachable_from, globs, fixed, regexp_mode, ci, with_clones, with_bridges, per_match_detail
):
    """Audit literal strings across the project: per-surface refs + verdict.

    Default mode treats each target as a literal fixed-string (ripgrep
    ``-F``). Pass ``-E`` / ``--regexp`` to treat each target as a regex
    (e.g. one ``setItem|removeItem|clear`` query covers three identifiers
    in a single pass). Regex mode is slower on large repos.

    Examples:

      \b
      roam refs-text DATABASE_URL
      roam refs-text /api/v1/users --reachable-from main
      roam refs-text -e foo.html -e bar.html        # multiple targets at once
      roam refs-text -E "setItem|removeItem|clear"   # W421 regex mode
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    targets = [s for s in (*strings, *extra) if s]
    if not targets:
        # Pattern 1B/1C discipline: emit a structured envelope in JSON mode
        # so MCP wrappers see actionable state, not a raw COMMAND_FAILED.
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "refs-text",
                        budget=token_budget,
                        summary={
                            "verdict": "no strings provided",
                            "state": "usage_error",
                            "partial_success": True,
                            "load_bearing": 0,
                        },
                        hint="Pass one or more strings as positional arguments or via -e.",
                        strings=[],
                        results=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: no strings provided")
            click.echo("Pass one or more strings as positional arguments or via -e.")
        raise SystemExit(2)

    ensure_index()
    root = find_project_root()
    glob_filter = list(globs) if globs else []

    # W421 — -E/--regexp opts into regex mode (ripgrep without -F); default
    # stays fixed-string for backward compatibility.
    fixed_mode = fixed and not regexp_mode

    # W607-I: Pattern-2 consumer-layer wiring — thread a warnings_out
    # bucket through the SUBPROCESS-shaped engine fan-out. cmd_refs_text
    # is the third subprocess-axis consumer (paired with cmd_grep W607-G
    # + cmd_history_grep W607-H). Three silent fallback locations in the
    # shared grep_helpers substrate (kept read-only per the task contract):
    #   1. ``detect_engine`` silently returns ``"fallback"`` when
    #      ROAM_GREP_ENGINE pins an absent binary → user pin not honored.
    #   2. ``run_search`` / ``_run_and_parse`` silently swallow
    #      FileNotFoundError + TimeoutExpired → return [] (looks like a
    #      no-match) while the subprocess never ran.
    #   3. Engine fallback re-labeling to "indexed_scan" happens silently
    #      when the fan-out fallthrough fires.
    # Marker family is ``refs_text_*`` (NOT ``grep_*`` / ``history_*`` /
    # ``search_*`` / ``complete_*`` / ``semantic_*``) — refs-text is the
    # string-audit-with-verdict axis, distinct shape from the sibling
    # subprocess consumers cmd_grep (W607-G) + cmd_history_grep (W607-H).
    # Empty bucket → byte-identical envelope (hash-stable). Non-empty
    # bucket → summary.warnings_out + summary.partial_success=True +
    # top-level mirror.
    #
    # Complementary (NOT a replacement) to the W805-W strict-xfail
    # Pattern-2 disclosure pins on the empty-corpus SAFE-TO-REMOVE path.
    # W805-W pins the empty-corpus state-disclosure axis (state="empty_corpus"
    # / partial_success=True / non-SAFE verdict). W607-I adds the
    # subprocess-degrade disclosure axis. The W805-W xfail-strict tests
    # MUST remain xfailed after W607-I lands.
    warnings_out: list[str] = []

    # --- Engine pin honoring check (W607-I outer-guard) ---
    # If the user pinned ROAM_GREP_ENGINE to a specific binary AND the
    # binary is not on PATH, ``detect_engine`` silently returns
    # ``"fallback"``. That's an unhonored pin — disclose it.
    _engine_pin = os.environ.get("ROAM_GREP_ENGINE", "auto").strip().lower()
    engine = detect_engine()
    if _engine_pin in {"ripgrep", "rg"} and engine != "ripgrep":
        warnings_out.append("refs_text_engine_pin_missing:ripgrep:binary 'rg' not on PATH (shutil.which returned None)")
    elif _engine_pin in {"git", "git-grep"} and engine != "git":
        warnings_out.append("refs_text_engine_pin_missing:git:binary 'git' not on PATH (shutil.which returned None)")

    used_engine = engine
    # --- Run engine (outer-guarded) ---
    try:
        all_matches = run_search(
            patterns=targets,
            root=root,
            globs=glob_filter,
            fixed_string=fixed_mode,  # W421
            case_insensitive=ci,
            engine=engine,
        )
    except Exception as exc:  # noqa: BLE001 — W607-I outer-guard
        if engine == "ripgrep":
            warnings_out.append(f"refs_text_ripgrep_failed:{type(exc).__name__}:{exc}")
        elif engine == "git":
            warnings_out.append(f"refs_text_git_grep_failed:{type(exc).__name__}:{exc}")
        else:
            warnings_out.append(f"refs_text_engine_failed:{type(exc).__name__}:{exc}")
        all_matches = []

    # Engine fallback to indexed-file scan.
    # W1010 lineage: when ``detect_engine`` returns ``"fallback"`` (no rg/git
    # on PATH) AND the indexed scan actually runs, relabel ``used_engine``
    # to ``"indexed_scan"`` so the envelope discloses which engine produced
    # the results. Mirrors the equivalent fix in ``cmd_grep`` so both
    # commands report the same engine vocabulary.
    if engine == "fallback":
        import re

        # W607-I: disclose the auto-fan-out fallthrough so the agent can
        # distinguish "no engines on PATH (fell through to indexed scan)"
        # from "engines present, just no matches".
        _rg_present = bool(shutil.which("rg"))
        _git_present = bool(shutil.which("git"))
        if not _rg_present and not _git_present:
            warnings_out.append("refs_text_engine_fanout_fallback:auto:neither 'rg' nor 'git' on PATH")
        flags = re.IGNORECASE if ci else 0
        compiled = [re.compile(re.escape(s) if fixed_mode else s, flags) for s in targets]  # W421
        try:
            with open_db(readonly=True) as conn_tmp:
                all_matches = indexed_file_scan(compiled, conn_tmp, root, glob_filter)
        except Exception as exc:  # noqa: BLE001 — W607-I outer-guard
            warnings_out.append(f"refs_text_indexed_scan_failed:{type(exc).__name__}:{exc}")
            all_matches = []
        used_engine = "indexed_scan"

    # Tag each match with which target string(s) it matches (literal/case-aware).
    _tag_matches(all_matches, targets, fixed=fixed_mode, ci=ci)  # W421

    if not all_matches:
        _emit_empty(json_mode, targets, token_budget, used_engine, warnings_out=warnings_out)
        return

    with open_db(readonly=True) as conn:
        match_paths = {m["path"] for m in all_matches}
        interval_idx = build_interval_index(conn, match_paths)
        for m in all_matches:
            sym = find_enclosing(interval_idx, m["path"], m["line"])
            m["_enclosing"] = sym
            m["enclosing_symbol"] = sym["qualified_name"] if sym else None
            m["enclosing_kind"] = sym["kind"] if sym else None

        # PageRank for every code-surface enclosing symbol
        pr_rows = conn.execute("SELECT symbol_id, pagerank FROM graph_metrics").fetchall()
        pr = {r["symbol_id"]: float(r["pagerank"] or 0.0) for r in pr_rows}
        for m in all_matches:
            sym = m.get("_enclosing")
            m["pagerank"] = pr.get(sym["id"], 0.0) if sym else 0.0

        # Reachability set (or orphan fallback)
        reach_set = build_reachable_set(conn, reachable_from) if reachable_from else None
        if reachable_from and reach_set is None:
            # Pattern 1B/1D: degraded resolution — anchor symbol not in
            # index. Emit a structured envelope so MCP wrappers see
            # actionable state instead of a raw COMMAND_FAILED.
            #
            # W607-I: also disclose the reachability degrade via
            # ``warnings_out`` so an agent scanning the bucket can detect
            # the silent-degrade lineage independently of the existing
            # ``state``/``resolution`` Pattern-1D disclosure.
            warnings_out.append(
                f"refs_text_reachability_degraded:unresolved_entry:entry symbol '{reachable_from}' not found in index"
            )
            msg = f"entry symbol '{reachable_from}' not found in index"
            if json_mode:
                _summary: dict = {
                    "verdict": msg,
                    "state": "unresolved_entry",
                    "partial_success": True,
                    "resolution": "unresolved",
                    "load_bearing": 0,
                }
                _extra: dict = {}
                if warnings_out:
                    _summary["warnings_out"] = list(warnings_out)
                    _extra["warnings_out"] = list(warnings_out)
                click.echo(
                    to_json(
                        json_envelope(
                            "refs-text",
                            budget=token_budget,
                            summary=_summary,
                            hint="Verify the symbol exists; try `roam search <name>` first.",
                            strings=list(targets),
                            results=[],
                            **_extra,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
            raise SystemExit(1)
        orphans = build_orphan_set(conn) if reach_set is None else set()

        clone_idx = build_clone_index(conn) if with_clones else {}
        bridge_idx = build_bridge_index(conn) if with_bridges else {}

        # Distribute matches into per-string buckets, by surface
        analyses: dict[str, dict] = {}
        for s in targets:
            analyses[s] = {
                "string": s,
                "total": 0,
                "surfaces": {},
            }

        for m in all_matches:
            for s in m["_matched_strings"]:
                bucket = analyses[s]
                # Per-match reachability annotation (set BEFORE classification so 'reachable' is correct on m)
                sym = m["_enclosing"]
                if reach_set is not None:
                    m["reachable"] = bool(sym and sym["id"] in reach_set)
                else:
                    m["reachable"] = bool(sym and sym["id"] not in orphans)

                surface = _classify_match(m, reach_set, orphans)
                bucket["surfaces"].setdefault(surface, []).append(m)
                bucket["total"] += 1

                # Annotate clone / bridge once per match (idempotent across buckets)
                if "_annotated" not in m:
                    if clone_idx and sym:
                        sibs = lookup_clone_siblings(clone_idx, sym, m["path"])
                        if sibs:
                            m["clone_siblings"] = sibs
                    if bridge_idx:
                        bl = bridge_idx.get(m["path"])
                        if bl:
                            m["bridge_links"] = bl
                    m["_annotated"] = True

    # --- Emit ---
    if json_mode:
        _emit_json(
            analyses,
            targets,
            token_budget,
            used_engine,
            reachable_from,
            per_match_detail,
            warnings_out=warnings_out,
        )
        return
    _emit_text(analyses, targets, reachable_from)


# ---------------------------------------------------------------------------
# Match tagging — which target strings does each match line cover?
# ---------------------------------------------------------------------------


def _tag_matches(matches, targets, *, fixed: bool, ci: bool) -> None:
    """In-place: add ``_matched_strings`` (list[str]) to each match."""
    if fixed:
        if ci:
            tg = [t.lower() for t in targets]
            for m in matches:
                content = m["content"].lower()
                m["_matched_strings"] = [orig for orig, t in zip(targets, tg) if t in content]
        else:
            for m in matches:
                content = m["content"]
                m["_matched_strings"] = [t for t in targets if t in content]
    else:
        import re

        rxs = [(t, re.compile(t, re.IGNORECASE if ci else 0)) for t in targets]
        for m in matches:
            m["_matched_strings"] = [t for t, rx in rxs if rx.search(m["content"])]
    # Ensure every match has at least one string (engine should not have returned otherwise)
    for m in matches:
        if not m["_matched_strings"]:
            m["_matched_strings"] = [targets[0]]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _emit_empty(json_mode, targets, budget, engine, *, warnings_out=None):
    """Engine returned zero matches — emit a SAFE-TO-REMOVE row per target.

    W607-I: when ``warnings_out`` is non-empty, surface the bucket on both
    summary and top-level (preserved-list-field discipline) and flip
    ``partial_success`` so agents can distinguish "engine ran cleanly, no
    matches" from "engine degraded / fanout fallback / pin missing".
    Empty bucket → byte-identical envelope (hash-stable).
    """
    results = [
        {
            "string": s,
            "verdict": "SAFE-TO-REMOVE",
            "reason": "no references in source code",
            "total": 0,
            "by_surface": {},
        }
        for s in targets
    ]
    if json_mode:
        _summary: dict = {
            "verdict": f"{len(targets)} string(s) checked, 0 load-bearing",
            "load_bearing": 0,
            "engine": engine,
        }
        _extra: dict = {}
        if warnings_out:
            _summary["warnings_out"] = list(warnings_out)
            _summary["partial_success"] = True
            _extra["warnings_out"] = list(warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "refs-text",
                    budget=budget,
                    summary=_summary,
                    strings=list(targets),
                    results=results,
                    **_extra,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {len(targets)} string(s) checked, 0 load-bearing")
        for s in targets:
            click.echo(f"--- {s} — SAFE-TO-REMOVE (no references in source code) ---")
            click.echo("  total references: 0")
            click.echo()


def _emit_json(analyses, targets, budget, engine, reachable_from, per_match_detail, *, warnings_out=None):
    """Emit the populated-matches envelope.

    W607-I: when ``warnings_out`` is non-empty, surface the bucket on both
    summary and top-level (preserved-list-field discipline) and flip
    ``partial_success`` so agents can detect subprocess-degrade lineage
    even on a fully successful audit. Empty bucket → byte-identical
    envelope (hash-stable).
    """
    results = []
    overall_load = 0
    for s in targets:
        a = analyses[s]
        verdict, reason = _verdict_for(a)
        if verdict == "LOAD-BEARING":
            overall_load += 1
        per_surface = {k: len(v) for k, v in a["surfaces"].items()}
        entry = {
            "string": s,
            "verdict": verdict,
            "reason": reason,
            "total": a["total"],
            "by_surface": per_surface,
        }
        if per_match_detail:
            entry["matches_by_surface"] = {
                surface: [_serialise_match(m) for m in items] for surface, items in a["surfaces"].items()
            }
        results.append(entry)
    summary: dict = {
        "verdict": f"{len(targets)} string(s) checked, {overall_load} load-bearing",
        "load_bearing": overall_load,
        "engine": engine,
        "reachable_from": reachable_from,
    }
    extra: dict = {}
    if warnings_out:
        summary["warnings_out"] = list(warnings_out)
        summary["partial_success"] = True
        extra["warnings_out"] = list(warnings_out)
    click.echo(
        to_json(
            json_envelope(
                "refs-text",
                budget=budget,
                summary=summary,
                strings=list(targets),
                results=results,
                **extra,
            )
        )
    )


def _serialise_match(m):
    out = {"path": m["path"], "line": m["line"], "content": m["content"]}
    for k in ("enclosing_symbol", "enclosing_kind", "reachable", "pagerank", "clone_siblings", "bridge_links"):
        if m.get(k) not in (None, [], {}):
            out[k] = m[k]
    return out


def _emit_text(analyses, targets, reachable_from):
    overall_load = sum(1 for s in targets if _verdict_for(analyses[s])[0] == "LOAD-BEARING")
    click.echo(f"VERDICT: {len(targets)} string(s) checked, {overall_load} load-bearing")
    if reachable_from:
        click.echo(f"  reachability anchored at entry: {reachable_from}")
    click.echo()
    for s in targets:
        a = analyses[s]
        verdict, reason = _verdict_for(a)
        click.echo(f"--- {s} — {verdict} ({reason}) ---")
        click.echo(f"  total references: {a['total']}")
        for surface, items in sorted(a["surfaces"].items()):
            click.echo(f"  {surface}: {len(items)}")
            for m in items[:3]:
                sym = m.get("enclosing_symbol")
                tag = ""
                if surface == "code":
                    tag = " [reachable]" if m.get("reachable") else " [unreachable]"
                bridges = m.get("bridge_links")
                clones = m.get("clone_siblings")
                extra = ""
                if bridges:
                    extra += f" bridges={len(bridges)}"
                if clones:
                    extra += f" clones={len(clones)}"
                click.echo(f"    - {loc(m['path'], m['line'])}{f' in {sym}' if sym else ''}{tag}{extra}")
            if len(items) > 3:
                click.echo(f"    ... +{len(items) - 3} more")
        click.echo()
