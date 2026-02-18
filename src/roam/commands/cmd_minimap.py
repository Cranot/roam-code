"""Generate a compact codebase minimap for CLAUDE.md injection."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


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
_COLLAPSE_AT_ROOT = frozenset({
    "benchmarks", "bench", ".github", "node_modules", "vendor",
    "dist", "build", "coverage", ".next", ".nuxt", "out", "target",
    ".cache", "tmp", "temp", "docs", "doc", ".idea", ".vscode",
})


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
        node[parts[-1]] = path   # leaf = original DB path
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
        "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
        "go": "Go", "rust": "Rust", "java": "Java", "csharp": "C#",
        "cpp": "C++", "c": "C", "php": "PHP", "ruby": "Ruby",
        "swift": "Swift", "kotlin": "Kotlin", "yaml": "YAML", "hcl": "HCL",
        "foxpro": "FoxPro", "apex": "Apex",
    }
    return " · ".join(nice.get(r["language"], r["language"].title()) for r in rows)


def _get_key_symbols(conn, limit: int = 5) -> list[str]:
    """Top symbols by PageRank (most central to the call graph)."""
    rows = conn.execute("""
        SELECT s.name, gm.pagerank
        FROM symbols s
        JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE s.is_exported = 1 AND gm.pagerank > 0
        ORDER BY gm.pagerank DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [f"`{r['name']}`" for r in rows]


def _get_touch_carefully(conn, min_in_degree: int = 15) -> list[str]:
    """Exported symbols with high fan-in — dangerous to rename or change signature."""
    rows = conn.execute("""
        SELECT s.name, gm.in_degree
        FROM symbols s
        JOIN graph_metrics gm ON s.id = gm.symbol_id
        WHERE gm.in_degree >= ? AND s.is_exported = 1
        ORDER BY gm.in_degree DESC
        LIMIT 8
    """, (min_in_degree,)).fetchall()
    return [f"`{r['name']}` ({r['in_degree']} callers)" for r in rows]


def _get_hotspots(conn, limit: int = 5) -> list[str]:
    """Files with highest churn * complexity score."""
    rows = conn.execute("""
        SELECT f.path,
               COALESCE(fs.total_churn, 0) * COALESCE(fs.complexity, 1.0) AS score
        FROM files f
        JOIN file_stats fs ON f.id = fs.file_id
        WHERE COALESCE(fs.total_churn, 0) > 0
        ORDER BY score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    result = []
    for r in rows:
        if r["score"] > 0:
            name = r["path"].replace("\\", "/").split("/")[-1]
            result.append(f"`{name}`")
    return result


def _get_conventions(conn) -> str:
    """Detect dominant naming conventions from the symbol table."""
    snake = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE kind='function' AND name LIKE '%_%'"
    ).fetchone()[0]
    camel = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE kind='function' AND name GLOB '*[A-Z]*' AND name NOT LIKE '%_%'"
    ).fetchone()[0]
    total_cls = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE kind='class'"
    ).fetchone()[0]
    pascal_cls = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE kind='class' AND LENGTH(name)>1 "
        "AND SUBSTR(name,1,1) = UPPER(SUBSTR(name,1,1))"
    ).fetchone()[0]

    parts: list[str] = []
    if snake > camel * 2:
        parts.append("snake_case fns")
    elif camel > snake * 2:
        parts.append("camelCase fns")
    else:
        parts.append("mixed fn style")

    if total_cls > 0 and pascal_cls / max(total_cls, 1) > 0.7:
        parts.append("PascalCase classes")

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

def _render_minimap(conn, root: Path) -> str:
    """Assemble the full minimap content (no sentinel wrappers)."""
    out: list[str] = []

    # Stack line
    stack = _get_stack(conn)
    if stack:
        out.append(f"**Stack:** {stack}")
        out.append("")

    # Annotated directory tree
    paths = [
        r["path"] for r in conn.execute("SELECT path FROM files ORDER BY path").fetchall()
    ]
    annotations = _get_file_annotations(conn)
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
    key = _get_key_symbols(conn, 5)
    if key:
        out.append(f"**Key symbols** (PageRank): {' · '.join(key)}")
        out.append("")

    # High fan-in: touch carefully
    touch = _get_touch_carefully(conn)
    if touch:
        out.append(f"**Touch carefully** (fan-in >= 15): {' · '.join(touch)}")
        out.append("")

    # Hotspots
    hotspots = _get_hotspots(conn)
    if hotspots:
        out.append(f"**Hotspots** (churn x complexity): {' · '.join(hotspots)}")
        out.append("")

    # Conventions
    conventions = _get_conventions(conn)
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

@click.command("minimap")
@click.option(
    "--update", "update_claude", is_flag=True, default=False,
    help="Update CLAUDE.md in-place (replaces sentinel block or appends).",
)
@click.option(
    "-o", "--output", "output_file", default=None, metavar="FILE",
    help="Write/update sentinel block in FILE (overrides --update target).",
)
@click.option(
    "--init-notes", is_flag=True, default=False,
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
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()

    # --init-notes: scaffold the notes file and exit
    if init_notes:
        notes_path = root / ".roam" / "minimap-notes.md"
        (root / ".roam").mkdir(exist_ok=True)
        if notes_path.exists():
            if json_mode:
                click.echo(to_json(json_envelope("minimap",
                    summary={"verdict": "exists", "path": str(notes_path)},
                )))
            else:
                click.echo(f"Already exists: {notes_path}")
                click.echo("Edit it to add project-specific gotchas for AI agents.")
        else:
            notes_path.write_text(_NOTES_TEMPLATE, encoding="utf-8")
            if json_mode:
                click.echo(to_json(json_envelope("minimap",
                    summary={"verdict": "created", "path": str(notes_path)},
                )))
            else:
                click.echo(f"Created: {notes_path}")
                click.echo("Edit it to add project-specific gotchas for AI agents.")
        return

    ensure_index()

    with open_db(readonly=True) as conn:
        content = _render_minimap(conn, root)

    block = _wrap_sentinels(content)

    # Determine target
    target: Path | None = None
    if output_file:
        p = Path(output_file)
        target = p if p.is_absolute() else root / p
    elif update_claude:
        target = root / "CLAUDE.md"

    if target is not None:
        verb = _upsert_file(target, block)
        if json_mode:
            click.echo(to_json(json_envelope("minimap",
                summary={"verdict": "ok", "action": verb.lower(), "file": str(target)},
                file=str(target),
            )))
        else:
            click.echo(f"{verb}: {target}")
    else:
        # Print to stdout
        if json_mode:
            click.echo(to_json(json_envelope("minimap",
                summary={"verdict": "ok"},
                content=block,
            )))
        else:
            click.echo(block)
