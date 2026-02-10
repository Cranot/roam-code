"""Auto-generate a CLAUDE.md project description from the index."""

import os
from collections import Counter

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json
from roam.commands.resolve import ensure_index


def _section_overview(conn):
    """Project overview: languages, file/symbol/edge counts."""
    lines = ["## Project Overview", ""]
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    files = conn.execute("SELECT language FROM files").fetchall()
    lang_counts = Counter(f["language"] for f in files if f["language"])
    lang_str = ", ".join(f"{lang} ({n})" for lang, n in lang_counts.most_common(8))

    lines.append(f"- **Files:** {total_files}")
    lines.append(f"- **Symbols:** {total_symbols}")
    lines.append(f"- **Edges:** {total_edges}")
    lines.append(f"- **Languages:** {lang_str}")
    return lines


def _section_directories(conn):
    """Directory structure with file counts and primary language."""
    lines = ["", "## Directory Structure", ""]
    rows = conn.execute("""
        SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
               THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
               ELSE '.' END as dir,
               COUNT(*) as cnt
        FROM files GROUP BY dir ORDER BY cnt DESC
    """).fetchall()

    # Get primary language per directory
    dir_langs = conn.execute("""
        SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
               THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
               ELSE '.' END as dir,
               language, COUNT(*) as cnt
        FROM files WHERE language IS NOT NULL
        GROUP BY dir, language ORDER BY dir, cnt DESC
    """).fetchall()
    primary_lang = {}
    for r in dir_langs:
        d = r["dir"]
        if d not in primary_lang:
            primary_lang[d] = r["language"]

    lines.append("| Directory | Files | Primary Language |")
    lines.append("|-----------|-------|------------------|")
    for r in rows[:20]:
        lang = primary_lang.get(r["dir"], "")
        lines.append(f"| `{r['dir']}/` | {r['cnt']} | {lang} |")
    return lines


def _section_entry_points(conn):
    """Entry points: conventional names + main() functions."""
    lines = ["", "## Entry Points", ""]
    entry_names = {
        "main.py", "__main__.py", "__init__.py", "index.js", "index.ts",
        "main.go", "main.rs", "app.py", "app.js", "app.ts",
        "mod.rs", "lib.rs", "setup.py", "manage.py",
    }
    files = conn.execute("SELECT path FROM files").fetchall()
    entries = [f["path"] for f in files
               if os.path.basename(f["path"]) in entry_names]

    main_files = conn.execute(
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = 'main' AND s.kind = 'function'",
    ).fetchall()
    for r in main_files:
        if r["path"] not in entries:
            entries.append(r["path"])

    if entries:
        for e in entries[:25]:
            lines.append(f"- `{e}`")
    else:
        lines.append("No conventional entry points detected.")
    return lines


def _section_key_abstractions(conn):
    """Top 15 symbols by PageRank."""
    lines = ["", "## Key Abstractions", ""]
    try:
        top = conn.execute("""
            SELECT s.name, s.kind, s.signature, f.path as file_path,
                   s.line_start, gm.pagerank
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            JOIN graph_metrics gm ON s.id = gm.symbol_id
            WHERE s.kind IN ('function', 'class', 'method', 'interface', 'struct', 'trait')
            ORDER BY gm.pagerank DESC LIMIT 15
        """).fetchall()
    except Exception:
        lines.append("Graph metrics not available. Run `roam index` first.")
        return lines

    if not top:
        lines.append("No graph metrics available.")
        return lines

    lines.append("Top symbols by importance (PageRank):")
    lines.append("")
    lines.append("| Symbol | Kind | Location |")
    lines.append("|--------|------|----------|")
    for s in top:
        sig = s["signature"] or ""
        if len(sig) > 50:
            sig = sig[:47] + "..."
        kind = s["kind"]
        name = s["name"]
        if sig:
            name = f"{name} {sig}"
        lines.append(f"| `{name}` | {kind} | `{s['file_path']}:{s['line_start']}` |")
    return lines


def _section_architecture(conn):
    """Layer count, shape, and cycle count."""
    lines = ["", "## Architecture", ""]
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        if len(G) == 0:
            lines.append("No graph data available.")
            return lines

        layers = detect_layers(G)
        cycles = find_cycles(G)

        max_layer = max(layers.values()) if layers else 0
        lines.append(f"- **Dependency layers:** {max_layer + 1}")
        lines.append(f"- **Cycles (SCCs):** {len(cycles)}")

        if layers:
            layer_counts = Counter(layers.values())
            lines.append(f"- **Layer distribution:** " +
                         ", ".join(f"L{k}: {v} symbols" for k, v in sorted(layer_counts.items())[:5]))
    except Exception:
        lines.append("Architecture analysis not available (graph module not loaded).")
    return lines


def _section_testing(conn):
    """Test directories, test file count, test-to-source ratio."""
    lines = ["", "## Testing", ""]
    test_patterns = ["test_", "_test.", ".test.", ".spec.", "tests/", "test/", "__tests__/", "spec/"]
    files = conn.execute("SELECT path FROM files").fetchall()
    all_paths = [f["path"].replace("\\", "/") for f in files]

    test_files = []
    source_files = []
    for p in all_paths:
        basename = os.path.basename(p)
        is_test = any(pat in basename for pat in ["test_", "_test.", ".test.", ".spec."]) or \
                  any(d in p for d in ["tests/", "test/", "__tests__/", "spec/"])
        if is_test:
            test_files.append(p)
        else:
            source_files.append(p)

    # Detect test directories
    test_dirs = set()
    for tf in test_files:
        parts = tf.split("/")
        for i, part in enumerate(parts):
            if part in ("tests", "test", "__tests__", "spec"):
                test_dirs.add("/".join(parts[:i + 1]))

    if test_dirs:
        lines.append("**Test directories:** " + ", ".join(f"`{d}/`" for d in sorted(test_dirs)))
    lines.append(f"- **Test files:** {len(test_files)}")
    lines.append(f"- **Source files:** {len(source_files)}")
    if source_files:
        ratio = len(test_files) / len(source_files)
        lines.append(f"- **Test-to-source ratio:** {ratio:.2f}")
    return lines


def _section_domain(conn):
    """Infer project domain from symbol names and manifest files."""
    import re
    lines = ["", "## Domain Keywords", ""]

    # Check for manifest files with descriptions
    root_files = conn.execute(
        "SELECT path FROM files WHERE path NOT LIKE '%/%'"
    ).fetchall()
    root_set = {f["path"] for f in root_files}

    project_root = None
    try:
        from roam.db.connection import find_project_root
        project_root = find_project_root()
    except Exception:
        pass

    if project_root:
        import json
        for manifest in ("package.json", "composer.json"):
            mpath = project_root / manifest
            if mpath.exists():
                try:
                    data = json.loads(mpath.read_text(encoding="utf-8"))
                    desc = data.get("description", "")
                    name = data.get("name", "")
                    if desc:
                        lines.append(f"- **Package:** {name}")
                        lines.append(f"- **Description:** {desc}")
                except Exception:
                    pass
                break

        for manifest in ("pyproject.toml",):
            mpath = project_root / manifest
            if mpath.exists():
                try:
                    text = mpath.read_text(encoding="utf-8")
                    for line in text.splitlines():
                        if line.strip().startswith("description"):
                            desc = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if desc:
                                lines.append(f"- **Description:** {desc}")
                            break
                except Exception:
                    pass
                break

    # Extract domain words from symbol names
    symbols = conn.execute(
        "SELECT name FROM symbols WHERE kind IN ('function', 'class', 'method', 'interface', 'struct') LIMIT 2000"
    ).fetchall()

    word_counts: dict[str, int] = {}
    _split_re = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')
    _stop = {
        # Generic programming
        "get", "set", "new", "init", "create", "make", "build", "run", "test",
        "handle", "process", "parse", "format", "check", "has", "the",
        "from", "with", "for", "add", "remove", "delete", "update", "find",
        "load", "save", "read", "write", "send", "resolve", "validate", "setup",
        "use", "can", "should", "will", "not", "all", "any", "map", "list",
        "item", "data", "type", "name", "value", "key", "index", "count",
        "result", "response", "request", "error", "node", "path", "base",
        "util", "utils", "helper", "helpers", "model", "models",
        # UI / framework
        "props", "emit", "emits", "ref", "computed", "watch", "reactive",
        "component", "view", "render", "mount", "unmount", "click",
        "submit", "change", "input", "select", "close", "open", "show",
        "hide", "toggle", "modal", "form", "field", "row", "col",
        "icon", "btn", "wrapper", "slot", "class", "style",
        "define", "expose", "provide", "inject",
        # Vue/React/Angular
        "state", "store", "action", "dispatch", "reducer",
        "effect", "callback", "memo", "context", "hook",
        # DOM events / UI interactions
        "click", "focus", "blur", "hover", "scroll", "resize",
        "keydown", "keyup", "keypress", "mousedown", "mouseup", "mouseover",
        "mouseenter", "mouseleave", "touchstart", "touchend", "touchmove",
        "drag", "drop", "dropdown", "popover", "tooltip", "overlay",
        "collapse", "expand", "visible", "disabled", "active", "selected",
        "loading", "loaded", "pending", "ready", "mounted", "updated",
        "before", "after", "enter", "leave", "transition", "animate",
        "width", "height", "size", "offset", "position", "layout",
        "container", "content", "header", "footer", "sidebar", "panel",
        "tab", "tabs", "menu", "nav", "link", "button", "label", "text",
        "image", "avatar", "badge", "card", "dialog", "drawer", "divider",
        # Generic code patterns
        "fetch", "query", "execute", "apply", "call", "bind",
        "start", "stop", "reset", "clear", "filter", "sort",
        "default", "config", "options", "params", "args",
    }
    for s in symbols:
        parts = _split_re.findall(s["name"])
        for p in parts:
            w = p.lower()
            if len(w) >= 3 and w not in _stop:
                word_counts[w] = word_counts.get(w, 0) + 1

    # Score: frequency × length bonus (longer words = more domain-specific)
    top_words = sorted(
        word_counts.items(),
        key=lambda x: -(x[1] * (1 + max(0, len(x[0]) - 4) * 0.15)),
    )[:20]
    if top_words:
        lines.append(f"- **Top domain terms:** {', '.join(w for w, _ in top_words)}")

    return lines


def _section_dependencies(conn):
    """Top imported files (most incoming file_edges)."""
    lines = ["", "## Core Modules", ""]
    try:
        top_deps = conn.execute("""
            SELECT f.path, COUNT(*) as import_count, SUM(fe.symbol_count) as total_symbols
            FROM file_edges fe
            JOIN files f ON fe.target_file_id = f.id
            GROUP BY fe.target_file_id
            ORDER BY import_count DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        lines.append("File edge data not available.")
        return lines

    if not top_deps:
        lines.append("No dependency data available.")
        return lines

    lines.append("Most-imported modules (everything depends on these):")
    lines.append("")
    lines.append("| Module | Imported By | Symbols Used |")
    lines.append("|--------|-------------|--------------|")
    for r in top_deps:
        lines.append(f"| `{r['path']}` | {r['import_count']} files | {r['total_symbols']} |")
    return lines


@click.command()
@click.option('--write', is_flag=True, help='Write output to CLAUDE.md in project root')
@click.option('--force', is_flag=True, help='Overwrite existing CLAUDE.md without confirmation')
@click.pass_context
def describe(ctx, write, force):
    """Auto-generate a project description (suitable for CLAUDE.md)."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        sections = []
        sections.append(["# Project Architecture", ""])
        sections.append(_section_overview(conn))
        sections.append(_section_directories(conn))
        sections.append(_section_entry_points(conn))
        sections.append(_section_key_abstractions(conn))
        sections.append(_section_architecture(conn))
        sections.append(_section_testing(conn))
        sections.append(_section_domain(conn))
        sections.append(_section_dependencies(conn))

        output = "\n".join(line for sec in sections for line in sec)

    if json_mode:
        click.echo(to_json({"markdown": output}))
        return

    if write:
        root = find_project_root()
        out_path = root / "CLAUDE.md"
        if out_path.exists() and not force:
            click.echo(f"CLAUDE.md already exists at {out_path}")
            click.echo("Use --force to overwrite, or omit --write to print to stdout.")
            return
        out_path.write_text(output, encoding="utf-8")
        click.echo(f"Wrote {out_path}")
    else:
        click.echo(output)
