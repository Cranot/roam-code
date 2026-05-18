"""Generate a compact codebase minimap for CLAUDE.md injection.

Naming-conventions detection delegates to the canonical helper in
``roam.commands.conventions_helper`` so the conventions line agrees
with ``roam describe``, ``roam understand``, ``roam preflight``, and
``roam conventions``.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because minimap outputs are invocation-scoped codebase skeleton
summaries — not per-location violations. Editor consumers should use
the JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist
+ W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.conventions_helper import compute_conventions
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Sentinel markers
# ---------------------------------------------------------------------------

_SENTINEL_START_RE = re.compile(r"<!-- roam:minimap[^\n]*-->")
_SENTINEL_END = "<!-- /roam:minimap -->"
_SENTINEL_BLOCK_RE = re.compile(
    r"<!-- roam:minimap[^\n]*-->\n.*?<!-- /roam:minimap -->",
    re.DOTALL,
)

_NOTES_TEMPLATE = """\
# Minimap Notes

Project-specific gotchas and architectural notes for AI agents.
Add bullet points below -- they appear in every `roam minimap` output.

## Gotchas

- (add notes here)

## Architecture Decisions

- (add notes here)

## Common Mistakes

- (add notes here)
"""


# ---------------------------------------------------------------------------
# Tree building
# ---------------------------------------------------------------------------

# Top-level directories that are not part of the main source and can be
# collapsed immediately to keep the minimap focused on the codebase.
_COLLAPSE_AT_ROOT = frozenset(
    {
        "benchmarks",
        "bench",
        ".github",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "coverage",
        ".next",
        ".nuxt",
        "out",
        "target",
        ".cache",
        "tmp",
        "temp",
        "docs",
        "doc",
        ".idea",
        ".vscode",
    }
)


def _count_files_in_tree(subtree: dict) -> int:
    count = 0
    for v in subtree.values():
        if isinstance(v, dict):
            count += _count_files_in_tree(v)
        else:
            count += 1
    return count


def _build_tree(file_paths: list[str]) -> dict:
    """Build a nested dict from a list of (possibly backslash) paths."""
    tree: dict = {}
    for path in sorted(file_paths):
        parts = path.replace("\\", "/").split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = path  # leaf = original DB path
    return tree


def _best_dir_hint(subtree: dict, annotations: dict[str, str]) -> str:
    """Return the first non-empty annotation found anywhere in subtree."""
    for k, v in sorted(subtree.items()):
        if isinstance(v, str):
            ann = annotations.get(v, "")
            if ann:
                return ann
        elif isinstance(v, dict):
            hint = _best_dir_hint(v, annotations)
            if hint:
                return hint
    return ""


def _render_tree(
    tree: dict,
    annotations: dict[str, str],
    prefix: str = "",
    depth: int = 0,
) -> list[str]:
    """Return annotated tree lines (no trailing newlines)."""
    lines: list[str] = []
    dirs = sorted((k, v) for k, v in tree.items() if isinstance(v, dict))
    files = sorted((k, v) for k, v in tree.items() if isinstance(v, str))

    for name, subtree in dirs:
        total = _count_files_in_tree(subtree)
        # Immediately collapse known non-source root directories
        if depth == 0 and name in _COLLAPSE_AT_ROOT:
            lines.append(f"{prefix}{name}/  ({total} files)")
            continue
        # Collapse large directories at depth >= 2 (commands/, languages/, etc.)
        if total > 8 and depth >= 2:
            hint = _best_dir_hint(subtree, annotations)
            note = f"  # {hint}" if hint else ""
            lines.append(f"{prefix}{name}/  ({total} files){note}")
        else:
            lines.append(f"{prefix}{name}/")
            lines.extend(_render_tree(subtree, annotations, prefix + "  ", depth + 1))

    for i, (name, path) in enumerate(files):
        # Show first 6 files; if more exist, collapse the rest
        if i >= 6 and len(files) > 8:
            lines.append(f"{prefix}... ({len(files) - 6} more)")
            break
        ann = annotations.get(path, "")
        if ann:
            lines.append(f"{prefix}{name:<24}# {ann}")
        else:
            lines.append(f"{prefix}{name}")

    return lines


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _get_file_annotations(conn) -> dict[str, str]:
    """Top 2 exported symbols by in_degree per file."""
    rows = conn.execute("""
        SELECT f.path, s.name, gm.in_degree
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE s.is_exported = 1
          AND s.kind IN ('function', 'class', 'method', 'module')
          AND gm.in_degree > 0
        ORDER BY f.path, gm.in_degree DESC
    """).fetchall()

    by_file: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_file[row["path"]].append(row["name"])

    return {path: ", ".join(syms[:2]) for path, syms in by_file.items()}


def _get_stack(conn) -> str:
    """Primary languages as a readable string."""
    rows = conn.execute("""
        SELECT language, COUNT(*) as cnt
        FROM files
        WHERE language IS NOT NULL AND language NOT IN ('generic')
        GROUP BY language
        ORDER BY cnt DESC
        LIMIT 6
    """).fetchall()
    nice = {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "go": "Go",
        "rust": "Rust",
        "java": "Java",
        "csharp": "C#",
        "cpp": "C++",
        "c": "C",
        "php": "PHP",
        "ruby": "Ruby",
        "swift": "Swift",
        "kotlin": "Kotlin",
        "yaml": "YAML",
        "hcl": "HCL",
        "foxpro": "FoxPro",
        "apex": "Apex",
    }
    return " · ".join(nice.get(r["language"], r["language"].title()) for r in rows)


def _get_key_symbols(conn, limit: int = 5) -> list[str]:
    """Top symbols by PageRank (most central to the call graph).

    Test-helper symbols (``invoke_cli``, ``cli_runner``, ``parse_json_output``)
    have huge fan-in because every test imports them, so they outrank actual
    source symbols on PageRank. They are test scaffolding, not project domain
    — skip files classified as ``test``. Mirrors ``cmd_tour._top_symbols``.
    """
    rows = conn.execute(
        """
        SELECT s.name, gm.pagerank
        FROM symbols s
        JOIN graph_metrics gm ON s.id = gm.symbol_id
        JOIN files f ON s.file_id = f.id
        WHERE s.is_exported = 1
          AND gm.pagerank > 0
          AND COALESCE(f.file_role, 'source') != 'test'
        ORDER BY gm.pagerank DESC
        LIMIT ?
    """,
        (limit,),
    ).fetchall()
    return [f"`{r['name']}`" for r in rows]


def _get_touch_carefully(conn, min_in_degree: int = 15) -> list[str]:
    """Exported symbols with high fan-in — dangerous to rename or change signature.

    Test-helper symbols are skipped via ``file_role != 'test'`` (see
    ``_get_key_symbols``); they inflate fan-in via every test-file import
    without being architecturally load-bearing.
    """
    rows = conn.execute(
        """
        SELECT s.name, gm.in_degree
        FROM symbols s
        JOIN graph_metrics gm ON s.id = gm.symbol_id
        JOIN files f ON s.file_id = f.id
        WHERE gm.in_degree >= ?
          AND s.is_exported = 1
          AND COALESCE(f.file_role, 'source') != 'test'
        ORDER BY gm.in_degree DESC
        LIMIT 8
    """,
        (min_in_degree,),
    ).fetchall()
    return [f"`{r['name']}` ({r['in_degree']} callers)" for r in rows]


def _get_hotspots(conn, limit: int = 5) -> list[str]:
    """Files with highest churn * complexity score (code-only by default)."""
    from roam.commands.cmd_understand import _hotspot_kind

    rows = conn.execute(
        """
        SELECT f.path,
               COALESCE(fs.total_churn, 0) * COALESCE(fs.complexity, 1.0) AS score
        FROM files f
        JOIN file_stats fs ON f.id = fs.file_id
        WHERE COALESCE(fs.total_churn, 0) > 0
        ORDER BY score DESC
        LIMIT ?
    """,
        (limit * 4,),
    ).fetchall()
    code_hits: list[str] = []
    other_hits: list[str] = []
    for r in rows:
        if r["score"] <= 0:
            continue
        name = r["path"].replace("\\", "/").split("/")[-1]
        if _hotspot_kind(r["path"]) == "code":
            code_hits.append(f"`{name}`")
        else:
            other_hits.append(f"`{name}`")
    take_code = min(len(code_hits), limit)
    remaining = limit - take_code
    return code_hits[:take_code] + other_hits[:remaining]


def _get_conventions(conn) -> str:
    """Detect dominant naming conventions from the symbol table.

    Delegates to the canonical detector in
    ``roam.commands.conventions_helper`` so the minimap's one-line
    summary is derived from the same per-kind percentages that
    ``roam describe`` / ``roam understand`` / ``roam conventions`` use.

    Previously this function collapsed the entire codebase to a single
    misleading label (e.g. ``"snake_case fns, PascalCase classes"``)
    that masked, for example, that 55% of methods were snake_case while
    93% of functions were camelCase — the same per-kind disagreement
    Pattern 4 of the dogfood corpus called out.
    """
    result = compute_conventions(conn)
    by_kind = result["by_kind"]

    parts: list[str] = []
    # Render per-kind so the minimap surfaces the same granularity
    # describe/understand do. Threshold at 60% so we don't pretend a
    # 51/49 split is a "convention", and label that case as mixed.
    for kind in ("function", "class", "method"):
        info = by_kind.get(kind)
        if not info:
            continue
        if info["pct"] >= 60:
            parts.append(f"{info['style']} {info['label']} ({info['pct']}%)")
        else:
            parts.append(f"mixed {info['label']}")

    return ", ".join(parts) if parts else "mixed conventions"


def _get_project_notes(root: Path) -> list[str]:
    """Load non-placeholder bullet points from .roam/minimap-notes.md."""
    notes_path = root / ".roam" / "minimap-notes.md"
    if not notes_path.exists():
        return []
    bullets = []
    for line in notes_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "(add notes here)" not in stripped:
            bullets.append(stripped)
    return bullets


# ---------------------------------------------------------------------------
# Minimap renderer
# ---------------------------------------------------------------------------


def _render_minimap(
    conn,
    root: Path,
    *,
    warnings_out: list[str] | None = None,
) -> str:
    """Assemble the full minimap content (no sentinel wrappers).

    W607-L: optional ``warnings_out`` accumulator threads DB-shape silent
    failures (substrate helper raises) out to the envelope. Each helper
    is wrapped per-phase so a single substrate failure does NOT abort
    the full minimap render — it surfaces a ``minimap_<phase>_failed:``
    marker and falls back to an empty section. Marker family
    ``minimap_*`` (DB-scope, distinct from W607-G/H/I/J subprocess
    families and W607-K's ``describe_*``).

    Complementary to W805-B strict-xfail Pattern-2 set (empty-corpus
    silent-SAFE verdict). W607-L does NOT graduate any W805-B bug — on
    an empty corpus the helpers return empty results (NOT exceptions),
    so warnings_out stays empty and the envelope is byte-identical to
    the pre-W607-L shape. Empty-corpus state disclosure is a separate
    Pattern-2 contract.
    """
    out: list[str] = []

    # Stack line
    try:
        stack = _get_stack(conn)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_stack_failed:{type(exc).__name__}:{exc}")
        stack = ""
    if stack:
        out.append(f"**Stack:** {stack}")
        out.append("")

    # Annotated directory tree (files + annotations are coupled)
    try:
        paths = [r["path"] for r in conn.execute("SELECT path FROM files ORDER BY path").fetchall()]
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_files_failed:{type(exc).__name__}:{exc}")
        paths = []
    try:
        annotations = _get_file_annotations(conn)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_annotations_failed:{type(exc).__name__}:{exc}")
        annotations = {}
    tree = _build_tree(paths)
    tree_lines = _render_tree(tree, annotations)

    out.append("```")
    # Cap at 45 lines to stay compact
    if len(tree_lines) <= 45:
        out.extend(tree_lines)
    else:
        out.extend(tree_lines[:45])
        out.append("...")
    out.append("```")
    out.append("")

    # Key symbols by PageRank
    try:
        key = _get_key_symbols(conn, 5)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_key_symbols_failed:{type(exc).__name__}:{exc}")
        key = []
    if key:
        out.append(f"**Key symbols** (PageRank): {' · '.join(key)}")
        out.append("")

    # High fan-in: touch carefully
    try:
        touch = _get_touch_carefully(conn)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_touch_carefully_failed:{type(exc).__name__}:{exc}")
        touch = []
    if touch:
        out.append(f"**Touch carefully** (fan-in >= 15): {' · '.join(touch)}")
        out.append("")

    # Hotspots
    try:
        hotspots = _get_hotspots(conn)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_hotspots_failed:{type(exc).__name__}:{exc}")
        hotspots = []
    if hotspots:
        out.append(f"**Hotspots** (churn x complexity): {' · '.join(hotspots)}")
        out.append("")

    # Conventions
    try:
        conventions = _get_conventions(conn)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"minimap_conventions_failed:{type(exc).__name__}:{exc}")
        conventions = "mixed conventions"
    out.append(f"**Conventions:** {conventions}")

    # Project-specific notes
    notes = _get_project_notes(root)
    if notes:
        out.append("")
        out.append("**Project notes:**")
        out.extend(notes)

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Sentinel helpers
# ---------------------------------------------------------------------------


def _wrap_sentinels(content: str) -> str:
    today = date.today().isoformat()
    return f"<!-- roam:minimap generated={today} -->\n{content}\n{_SENTINEL_END}"


def _upsert_file(filepath: Path, block: str) -> str:
    """Replace sentinel block if present, otherwise append. Returns verb."""
    if not filepath.exists():
        filepath.write_text(block + "\n", encoding="utf-8")
        return "Created"
    text = filepath.read_text(encoding="utf-8")
    if _SENTINEL_BLOCK_RE.search(text):
        new_text = _SENTINEL_BLOCK_RE.sub(block, text)
        filepath.write_text(new_text, encoding="utf-8")
        return "Updated"
    # No existing sentinel — append
    filepath.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    return "Appended to"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="minimap",
    category="getting-started",
    summary="Generate a compact codebase minimap for CLAUDE.md injection",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("minimap")
@click.option(
    "--update",
    "update_claude",
    is_flag=True,
    default=False,
    help="Update CLAUDE.md in-place (replaces sentinel block or appends).",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    default=None,
    metavar="FILE",
    help="Write/update sentinel block in FILE (overrides --update target).",
)
@click.option(
    "--init-notes",
    is_flag=True,
    default=False,
    help="Scaffold .roam/minimap-notes.md for project-specific gotchas.",
)
@click.pass_context
def minimap(ctx, update_claude, output_file, init_notes):
    """Generate a compact codebase minimap for CLAUDE.md injection.

    Outputs a ~20-line annotated snapshot: stack, directory tree with symbol
    annotations, key symbols by PageRank, high fan-in symbols to avoid touching,
    hotspots (churn x complexity), detected conventions, and optional project notes.

    \b
    Usage:
      roam minimap                    # print to stdout
      roam minimap --update           # refresh CLAUDE.md in-place
      roam minimap -o docs/AGENTS.md  # target a different file
      roam minimap --init-notes       # scaffold .roam/minimap-notes.md

    The sentinel pair <!-- roam:minimap --> ... <!-- /roam:minimap --> is
    replaced on each run, leaving surrounding content intact.

    Add project-specific gotchas to .roam/minimap-notes.md -- they appear
    in every subsequent minimap output.

    The generated snapshot includes tech stack, annotated directory tree,
    key symbols by PageRank, high-fan-in symbols, and hotspots.  Use
    ``--update`` to refresh the sentinel block in-place without clobbering
    surrounding content.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    # --init-notes: scaffold the notes file and exit
    if init_notes:
        notes_path = root / ".roam" / "minimap-notes.md"
        (root / ".roam").mkdir(exist_ok=True)
        if notes_path.exists():
            if json_mode:
                # LAW 6: verdict works without any other field.
                click.echo(
                    to_json(
                        json_envelope(
                            "minimap",
                            summary={
                                "verdict": f"minimap notes file already exists at {notes_path}",
                                "action": "exists",
                                "path": str(notes_path),
                            },
                        )
                    )
                )
            else:
                click.echo(f"Already exists: {notes_path}")
                click.echo("Edit it to add project-specific gotchas for AI agents.")
        else:
            notes_path.write_text(_NOTES_TEMPLATE, encoding="utf-8")
            if json_mode:
                # LAW 6: verdict works without any other field.
                click.echo(
                    to_json(
                        json_envelope(
                            "minimap",
                            summary={
                                "verdict": f"minimap notes file created at {notes_path}",
                                "action": "created",
                                "path": str(notes_path),
                            },
                        )
                    )
                )
            else:
                click.echo(f"Created: {notes_path}")
                click.echo("Edit it to add project-specific gotchas for AI agents.")
        return

    ensure_index()

    # W607-L: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the DB-shape minimap pipeline. cmd_minimap is a
    # DB-shape aggregator that consumes graph_metrics / symbols / files /
    # file_stats / file_edges substrates via per-helper SQL queries; any
    # of those raising silently degrades the rendered block into an empty
    # section while the JSON envelope claims success. The W607-L
    # outer-guard + per-helper marker thread makes the degrade lineage
    # visible to consumers independent of the markdown blob. Marker
    # family ``minimap_*`` (DB scope, distinct from W607-G/H/I/J grep_* /
    # history_* / refs_text_* / delete_check_* subprocess families and
    # W607-K's ``describe_*`` flagship-aggregator family).
    #
    # Complementary to W805-B strict-xfail Pattern-2 set (which pins the
    # empty-corpus silent-SAFE "minimap rendered (N chars)" verdict).
    # W607-L does NOT graduate any W805-B bug — empty-corpus state
    # disclosure is a separate Pattern-2 contract orthogonal to the
    # DB-shape degrade axis here. On empty corpus the helpers return
    # empty results (NOT exceptions) so warnings_out stays empty and the
    # envelope is byte-identical to the pre-W607-L shape.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either ``summary`` or top-level).
    warnings_out: list[str] = []

    # W607-AZ: per-phase substrate-CALL marker plumbing (ADDITIVE to W607-L's
    # outer-guard + per-section-helper bare try/except family). cmd_minimap
    # is a high-traffic exploration aggregator that builds a navigable map
    # the next-action agent uses for orientation. W607-L wrapped the
    # per-section DB-shape helpers inside ``_render_minimap``; W607-AZ adds
    # the canonical ``_run_check_az`` closure-based wrapper covering the
    # downstream NON-DB substrate boundaries — markdown-sentinel wrap,
    # filesystem upsert (``--update`` / ``-o``), and JSON serialization.
    # A raise in any of those previously bubbled as a Click traceback;
    # W607-AZ surfaces them as structured
    # ``minimap_<phase>_failed:<exc_class>:<detail>`` markers and falls back
    # to safe defaults. Same ``minimap_*`` family as W607-L (closed-enum
    # marker-prefix discipline preserved). Mirrors the canonical W607-AV
    # additive template (cmd_dogfood) — additive bucket merged into the
    # canonical ``warnings_out`` channel below.
    _w607az_warnings_out: list[str] = []

    def _run_check_az(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-AZ marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``minimap_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607az_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607az_warnings_out.append(f"minimap_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    try:
        with open_db(readonly=True) as conn:
            content = _render_minimap(conn, root, warnings_out=warnings_out)
    except Exception as exc:
        # W607-L outer-guard: the full minimap DB pipeline raised (db
        # corruption / locked / schema drift). Disclose via
        # ``minimap_pipeline_failed:...`` + fall back to empty content.
        warnings_out.append(f"minimap_pipeline_failed:{type(exc).__name__}:{exc}")
        content = ""

    # W607-AZ: wrap sentinel-block construction. Today _wrap_sentinels is a
    # trivial f-string, but future surface evolution (e.g. embedding repo
    # metadata, git SHA, generated_by token) could raise on missing inputs.
    block = _run_check_az("wrap_sentinels", _wrap_sentinels, content, default="")

    # Determine target
    target: Path | None = None
    if output_file:
        p = Path(output_file)
        target = p if p.is_absolute() else root / p
    elif update_claude:
        target = root / "CLAUDE.md"

    if target is not None:
        # W607-AZ: wrap filesystem upsert. _upsert_file performs read +
        # regex-substitute + write; any I/O substrate raise (permission
        # denied, read-only filesystem, encoding error) previously bubbled
        # as a Click traceback. Default to "Failed" verb on substrate raise.
        verb = _run_check_az("upsert_file", _upsert_file, target, block, default="Failed")

    # W607-AZ: merge per-phase bucket into the canonical W607-L
    # ``warnings_out`` channel. Same marker-family prefix (``minimap_*``);
    # the two buckets share a single envelope channel so consumers don't
    # need to know which sub-wave (L or AZ) produced any individual marker.
    combined_warnings = list(warnings_out) + list(_w607az_warnings_out)

    if target is not None:
        if json_mode:
            # LAW 4 (W17.3): concrete verb + path beats bare ``"ok"``.
            mm_summary: dict[str, object] = {
                "verdict": f"minimap {verb.lower()} in {target}",
                "action": verb.lower(),
                "file": str(target),
            }
            # W607-L + W607-AZ: surface combined marker bucket on summary
            # mirror + top-level so the substrate-degrade lineage is
            # visible to consumers reading either the summary block or the
            # preserved-list top-level field.
            if combined_warnings:
                mm_summary["warnings_out"] = list(combined_warnings)
                mm_summary["partial_success"] = True
            envelope_text = _run_check_az(
                "serialize_envelope",
                lambda: to_json(
                    json_envelope(
                        "minimap",
                        summary=mm_summary,
                        file=str(target),
                        **({"warnings_out": list(combined_warnings)} if combined_warnings else {}),
                    )
                ),
                default=None,
            )
            if envelope_text is None:
                # serialize_envelope raised — re-merge bucket (it grew) and
                # produce a minimal envelope so the contract still holds.
                combined_warnings = list(warnings_out) + list(_w607az_warnings_out)
                mm_summary["warnings_out"] = list(combined_warnings)
                mm_summary["partial_success"] = True
                envelope_text = to_json(
                    json_envelope(
                        "minimap",
                        summary=mm_summary,
                        file=str(target),
                        warnings_out=list(combined_warnings),
                    )
                )
            click.echo(envelope_text)
        else:
            click.echo(f"{verb}: {target}")
    else:
        # Print to stdout
        if json_mode:
            mm_summary = {
                # LAW 4 (W17.3): name the analytical subject
                # (the rendered block) + a size cue, not bare "ok".
                "verdict": (f"minimap rendered ({len(block)} chars) — wrap in CLAUDE.md with --update-claude"),
                "content_char_count": len(block),
                "caller_metric_definition": "direct_in_degree (Touch carefully + file annotations)",
            }
            # W607-L + W607-AZ: surface combined marker bucket on summary
            # mirror + top-level on the stdout JSON-mode path. Note: we DO
            # flip partial_success here when substrate failures fire, but
            # this is orthogonal to W805-B's empty-corpus xfail-strict
            # tests — on empty corpus the helpers return empty results
            # (not exceptions), so combined_warnings is empty and
            # partial_success is NOT flipped, preserving W805-B's pinned
            # bug-state.
            if combined_warnings:
                mm_summary["warnings_out"] = list(combined_warnings)
                mm_summary["partial_success"] = True
            envelope_text = _run_check_az(
                "serialize_envelope",
                lambda: to_json(
                    json_envelope(
                        "minimap",
                        summary=mm_summary,
                        content=block,
                        **({"warnings_out": list(combined_warnings)} if combined_warnings else {}),
                    )
                ),
                default=None,
            )
            if envelope_text is None:
                combined_warnings = list(warnings_out) + list(_w607az_warnings_out)
                mm_summary["warnings_out"] = list(combined_warnings)
                mm_summary["partial_success"] = True
                envelope_text = to_json(
                    json_envelope(
                        "minimap",
                        summary=mm_summary,
                        content=block,
                        warnings_out=list(combined_warnings),
                    )
                )
            click.echo(envelope_text)
        else:
            click.echo(block)
