"""Auto-generate a project description for AI coding agents."""

from __future__ import annotations

import os
from collections import Counter

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
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


def _read_manifest_info(project_root):
    """Read package name/description from manifest files."""
    if not project_root:
        return []
    import json
    lines = []
    for manifest in ("package.json", "composer.json"):
        mpath = project_root / manifest
        if not mpath.exists():
            continue
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
        if not mpath.exists():
            continue
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
    return lines


_DOMAIN_STOP_WORDS = frozenset({
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
})


def _section_domain(conn):
    """Infer project domain from symbol names and manifest files."""
    import re
    lines = ["", "## Domain Keywords", ""]

    project_root = None
    try:
        from roam.db.connection import find_project_root
        project_root = find_project_root()
    except Exception:
        pass

    lines.extend(_read_manifest_info(project_root))

    symbols = conn.execute(
        "SELECT name FROM symbols WHERE kind IN ('function', 'class', 'method', 'interface', 'struct') LIMIT 2000"
    ).fetchall()

    word_counts: dict[str, int] = {}
    _split_re = re.compile(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)')
    for s in symbols:
        for p in _split_re.findall(s["name"]):
            w = p.lower()
            if len(w) >= 3 and w not in _DOMAIN_STOP_WORDS:
                word_counts[w] = word_counts.get(w, 0) + 1

    top_words = sorted(
        word_counts.items(),
        key=lambda x: -(x[1] * (1 + max(0, len(x[0]) - 4) * 0.15)),
    )[:20]
    if top_words:
        lines.append(f"- **Top domain terms:** {', '.join(w for w, _ in top_words)}")

    return lines


def _section_conventions(conn):
    """Document detected coding conventions for agents to follow."""
    import re
    lines = ["", "## Coding Conventions", ""]
    lines.append("Follow these conventions when writing code in this project:")
    lines.append("")

    _SNAKE = re.compile(r'^[a-z_][a-z0-9_]*$')
    _CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
    _PASCAL = re.compile(r'^[A-Z][a-zA-Z0-9]*$')

    for kind, label in [("function", "Functions"), ("class", "Classes"), ("method", "Methods")]:
        rows = conn.execute("SELECT name FROM symbols WHERE kind = ?", (kind,)).fetchall()
        if not rows:
            continue
        names = [r["name"] for r in rows]
        counts = {"snake_case": 0, "camelCase": 0, "PascalCase": 0}
        for n in names:
            if _PASCAL.match(n):
                counts["PascalCase"] += 1
            elif _SNAKE.match(n):
                counts["snake_case"] += 1
            elif _CAMEL.match(n):
                counts["camelCase"] += 1
        dominant = max(counts, key=counts.get)
        total = len(names)
        pct = round(counts[dominant] * 100 / total) if total else 0
        if pct >= 80:
            kind_plural = "classes" if kind == "class" else f"{kind}s"
            lines.append(f"- **{label}:** Use `{dominant}` ({pct}% of {total} {kind_plural})")

    # Import style
    try:
        total_edges = conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()[0]
        cross_dir = conn.execute(
            "SELECT COUNT(*) FROM file_edges fe "
            "JOIN files sf ON fe.source_file_id = sf.id "
            "JOIN files tf ON fe.target_file_id = tf.id "
            "WHERE sf.path NOT LIKE tf.path || '%'"
        ).fetchone()[0]
        if total_edges > 0:
            pct = round(cross_dir * 100 / total_edges)
            if pct > 60:
                lines.append(f"- **Imports:** Prefer absolute imports ({pct}% are cross-directory)")
            else:
                lines.append(f"- **Imports:** Relative imports common ({100-pct}% same-directory)")
    except Exception:
        pass

    # Test patterns
    test_files = conn.execute(
        "SELECT path FROM files WHERE path LIKE '%test%'"
    ).fetchall()
    if test_files:
        patterns = set()
        for r in test_files:
            name = r["path"].replace("\\", "/").split("/")[-1]
            if name.startswith("test_"):
                patterns.add("test_*.py")
            elif name.endswith("_test.py"):
                patterns.add("*_test.py")
            elif ".test." in name:
                patterns.add("*.test.*")
            elif ".spec." in name:
                patterns.add("*.spec.*")
        if patterns:
            lines.append(f"- **Test files:** {', '.join(sorted(patterns))}")

    return lines


def _section_complexity_guide(conn):
    """Document complexity hotspots to guide refactoring."""
    lines = ["", "## Complexity Hotspots", ""]

    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, AVG(cognitive_complexity) as avg_cc "
            "FROM symbol_metrics"
        ).fetchone()
        if not row or row["total"] == 0:
            return lines

        lines.append(
            f"Average function complexity: {row['avg_cc']:.1f} "
            f"({row['total']} functions analyzed)"
        )
        lines.append("")

        critical = conn.execute(
            "SELECT s.name, sm.cognitive_complexity, f.path, s.line_start "
            "FROM symbol_metrics sm "
            "JOIN symbols s ON sm.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE sm.cognitive_complexity >= 25 "
            "ORDER BY sm.cognitive_complexity DESC LIMIT 10"
        ).fetchall()

        if critical:
            lines.append("Functions with highest complexity (consider refactoring):")
            lines.append("")
            lines.append("| Function | Complexity | Location |")
            lines.append("|----------|-----------|----------|")
            for r in critical:
                lines.append(
                    f"| `{r['name']}` | {r['cognitive_complexity']:.0f} "
                    f"| `{r['path']}:{r['line_start']}` |"
                )
    except Exception:
        pass

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


def _agent_prompt_data(conn):
    """Gather compact data for --agent-prompt output."""
    import re

    data = {}

    # ── Project overview ─────────────────────────────────────────────
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    files = conn.execute("SELECT language FROM files").fetchall()
    lang_counts = Counter(f["language"] for f in files if f["language"])
    languages = ", ".join(lang for lang, _ in lang_counts.most_common(5))

    # Infer project name from project root directory
    try:
        root = find_project_root()
        project_name = root.name
    except Exception:
        project_name = "unknown"

    data["project"] = project_name
    data["files"] = total_files
    data["symbols"] = total_symbols
    data["languages"] = languages

    # ── Stack / key dependencies (from most-imported files) ──────────
    try:
        top_imports = conn.execute("""
            SELECT f.path, COUNT(*) as import_count
            FROM file_edges fe
            JOIN files f ON fe.target_file_id = f.id
            GROUP BY fe.target_file_id
            ORDER BY import_count DESC
            LIMIT 10
        """).fetchall()
        stack_items = []
        for r in top_imports:
            p = r["path"].replace("\\", "/")
            # Use top-level directory or filename as dependency hint
            parts = p.split("/")
            stack_items.append(parts[0] if len(parts) > 1 else p)
        # Deduplicate preserving order
        seen = set()
        stack_deduped = []
        for s in stack_items:
            if s not in seen:
                seen.add(s)
                stack_deduped.append(s)
        data["stack"] = ", ".join(stack_deduped[:8])
    except Exception:
        data["stack"] = ""

    # ── Conventions ──────────────────────────────────────────────────
    _SNAKE = re.compile(r'^[a-z_][a-z0-9_]*$')
    _CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
    _PASCAL = re.compile(r'^[A-Z][a-zA-Z0-9]*$')

    conventions = []
    for kind, label in [("function", "functions"), ("class", "classes"), ("method", "methods")]:
        rows = conn.execute("SELECT name FROM symbols WHERE kind = ?", (kind,)).fetchall()
        if not rows:
            continue
        counts = {"snake_case": 0, "camelCase": 0, "PascalCase": 0}
        for r in rows:
            n = r["name"]
            if _PASCAL.match(n):
                counts["PascalCase"] += 1
            elif _SNAKE.match(n):
                counts["snake_case"] += 1
            elif _CAMEL.match(n):
                counts["camelCase"] += 1
        dominant = max(counts, key=counts.get)
        total = len(rows)
        pct = round(counts[dominant] * 100 / total) if total else 0
        if pct >= 70:
            conventions.append(f"{label}={dominant}")
    data["conventions"] = ", ".join(conventions) if conventions else "mixed"

    # ── Directory structure ──────────────────────────────────────────
    dir_rows = conn.execute("""
        SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
               THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
               ELSE '.' END as dir,
               COUNT(*) as cnt
        FROM files GROUP BY dir ORDER BY cnt DESC
    """).fetchall()
    dir_parts = [f"{r['dir']}/ ({r['cnt']})" for r in dir_rows[:8]]
    data["structure"] = ", ".join(dir_parts)

    # ── Key abstractions (top 5 by PageRank) ─────────────────────────
    abstractions = []
    try:
        top = conn.execute("""
            SELECT s.name, s.kind, f.path as file_path, gm.pagerank
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            JOIN graph_metrics gm ON s.id = gm.symbol_id
            WHERE s.kind IN ('function', 'class', 'method', 'interface', 'struct', 'trait')
            ORDER BY gm.pagerank DESC LIMIT 5
        """).fetchall()
        for s in top:
            abstractions.append(f"{s['name']} ({s['kind']}, {s['file_path']})")
    except Exception:
        pass
    data["key_abstractions"] = abstractions

    # ── Hotspots (top 3 by churn * complexity) ───────────────────────
    hotspots = []
    try:
        rows = conn.execute("""
            SELECT s.name, f.path, sm.cognitive_complexity,
                   COALESCE(gs.commit_count, 0) as churn
            FROM symbol_metrics sm
            JOIN symbols s ON sm.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            LEFT JOIN git_stats gs ON f.id = gs.file_id
            WHERE sm.cognitive_complexity > 0
            ORDER BY (sm.cognitive_complexity * COALESCE(gs.commit_count, 1)) DESC
            LIMIT 3
        """).fetchall()
        for r in rows:
            hotspots.append(
                f"{r['name']} (complexity={r['cognitive_complexity']:.0f}, "
                f"churn={r['churn']}, {r['path']})"
            )
    except Exception:
        pass
    data["hotspots"] = hotspots

    # ── Health score + cycles ────────────────────────────────────────
    data["health_score"] = "N/A"
    data["cycles"] = "N/A"
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
        G = build_symbol_graph(conn)
        total_syms = len(G)
        if total_syms > 0:
            cycles = find_cycles(G)
            cycle_syms = sum(len(c) for c in cycles)
            cycle_pct = (cycle_syms / total_syms * 100) if total_syms else 0
            score = max(0, 100 - int(cycle_pct * 2))
            data["health_score"] = score
            data["cycles"] = len(cycles)
    except Exception:
        pass

    # ── Test command guess ───────────────────────────────────────────
    test_files = conn.execute(
        "SELECT path FROM files WHERE path LIKE '%test%' LIMIT 1"
    ).fetchall()
    if test_files:
        p = test_files[0]["path"].replace("\\", "/")
        if "tests/" in p:
            data["test_cmd"] = "pytest tests/"
        elif "test/" in p:
            data["test_cmd"] = "pytest test/"
        elif ".spec." in p or ".test." in p:
            data["test_cmd"] = "npm test"
        else:
            data["test_cmd"] = "pytest"
    else:
        data["test_cmd"] = ""

    return data


def _format_agent_prompt(data: dict) -> str:
    """Format agent-prompt data as compact plain text."""
    lines = []
    lines.append(
        f"Project: {data['project']} "
        f"({data['files']} files, {data['symbols']} symbols, {data['languages']})"
    )
    if data.get("stack"):
        lines.append(f"Stack: {data['stack']}")
    lines.append(f"Conventions: {data['conventions']}")
    lines.append(f"Structure: {data['structure']}")

    if data.get("key_abstractions"):
        lines.append("Key abstractions:")
        for a in data["key_abstractions"]:
            lines.append(f"  - {a}")

    if data.get("hotspots"):
        lines.append("Hotspots:")
        for h in data["hotspots"]:
            lines.append(f"  - {h}")

    health = data.get("health_score", "N/A")
    cycles = data.get("cycles", "N/A")
    lines.append(f"Health: {health}/100, {cycles} cycles")

    if data.get("test_cmd"):
        lines.append(f"Test cmd: {data['test_cmd']}")

    return "\n".join(lines)


# Agent config file detection order — first existing file wins.
# If none exist, fall back to CLAUDE.md (most common).
_AGENT_CONFIG_FILES = [
    "CLAUDE.md",                          # Claude Code
    "AGENTS.md",                          # OpenAI Codex CLI
    "GEMINI.md",                          # Gemini CLI
    ".cursor/rules/roam.mdc",            # Cursor
    ".windsurf/rules/roam.md",           # Windsurf
    ".github/copilot-instructions.md",   # GitHub Copilot
    "CONVENTIONS.md",                     # Aider / generic
    ".clinerules/roam.md",               # Cline
]


def _detect_agent_config(root: "Path") -> "Path":
    """Auto-detect the right agent config file in the project.

    Checks for existing AI tool config files in priority order.
    If one already exists, returns it (so ``--write`` updates the right file).
    Otherwise defaults to ``CLAUDE.md``.
    """
    for rel in _AGENT_CONFIG_FILES:
        candidate = root / rel
        if candidate.exists():
            return candidate
    return root / "CLAUDE.md"


@click.command()
@click.option('--write', is_flag=True, help='Write output to the detected agent config file')
@click.option('--force', is_flag=True, help='Overwrite existing file without confirmation')
@click.option('--agent-prompt', is_flag=True, help='Compact agent-oriented prompt (under 500 tokens)')
@click.option('-o', '--output', 'out_file', default=None,
              help='Explicit output path (overrides auto-detection)')
@click.pass_context
def describe(ctx, write, force, agent_prompt, out_file):
    """Auto-generate a project description for AI coding agents.

    By default prints to stdout.  Use ``--write`` to save to your agent's
    config file (auto-detected: CLAUDE.md, AGENTS.md, .cursor/rules, etc.)
    or ``-o PATH`` to specify an explicit output path.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()

    if agent_prompt:
        with open_db(readonly=True) as conn:
            data = _agent_prompt_data(conn)

        if json_mode:
            click.echo(to_json(json_envelope("describe",
                summary={"mode": "agent-prompt"},
                **data,
            )))
        else:
            click.echo(_format_agent_prompt(data))
        return

    with open_db(readonly=True) as conn:
        sections = []
        sections.append(["# Project Architecture", ""])
        sections.append(_section_overview(conn))
        sections.append(_section_directories(conn))
        sections.append(_section_entry_points(conn))
        sections.append(_section_key_abstractions(conn))
        sections.append(_section_architecture(conn))
        sections.append(_section_testing(conn))
        sections.append(_section_conventions(conn))
        sections.append(_section_complexity_guide(conn))
        sections.append(_section_domain(conn))
        sections.append(_section_dependencies(conn))

        output = "\n".join(line for sec in sections for line in sec)

    if json_mode:
        click.echo(to_json(json_envelope("describe",
            summary={"length": len(output)},
            markdown=output,
        )))
        return

    if write or out_file:
        from pathlib import Path
        root = find_project_root()
        if out_file:
            out_path = Path(out_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path = _detect_agent_config(root)
        if out_path.exists() and not force:
            click.echo(f"{out_path.name} already exists at {out_path}")
            click.echo("Use --force to overwrite, or omit --write to print to stdout.")
            return
        out_path.write_text(output, encoding="utf-8")
        click.echo(f"Wrote {out_path}")
    else:
        click.echo(output)
