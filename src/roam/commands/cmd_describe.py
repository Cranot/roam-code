"""Auto-generate a project description for AI coding agents.

Prefer ``roam understand --agent`` for the compact token-efficient variant
(equivalent to ``roam describe --agent-prompt``), or plain ``roam understand``
for the full architecture overview.

This command is kept as a standalone entry point for its ``--write`` /
``-o PATH`` flags, which persist the project description to an agent config
file (CLAUDE.md, AGENTS.md, .cursor/rules, etc.).

Helper functions in this module (``_agent_prompt_data``,
``_format_agent_prompt``) are imported by ``roam.commands.cmd_understand``
and must not be removed.

Naming-conventions detection lives in
``roam.commands.conventions_helper`` (the canonical detector shared by
describe, understand, minimap, preflight, and the standalone
``conventions`` command). Do not re-implement it here.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because describe outputs are invocation-scoped symbol metadata
records — not per-location violations. Editor consumers should use the
JSON envelope directly. See action.yml _SUPPORTED_SARIF allowlist +
W1175-RESEARCH Bucket B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.conventions_helper import (
    compute_conventions,
    short_conventions_string,
)
from roam.commands.resolve import detect_entry_points, ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json


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
    """Entry points: conventional names, main() functions, and route/command decorators."""
    lines = ["", "## Entry Points", ""]
    entries = [ep["path"] for ep in detect_entry_points(conn)]
    if entries:
        for e in entries[:25]:
            lines.append(f"- `{e}`")
    else:
        lines.append("No conventional entry points detected.")
    return lines


def _section_key_abstractions(conn, *, warnings_out: list[str] | None = None):
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
    except Exception as exc:
        # W607-K: surface the DB-shape failure via warnings_out so the
        # outer envelope can mirror the marker; complementary to the
        # human-readable "Graph metrics not available." text.
        if warnings_out is not None:
            warnings_out.append(f"describe_key_abstractions_failed:{type(exc).__name__}:{exc}")
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


def _section_architecture(conn, *, warnings_out: list[str] | None = None):
    """Layer count, shape, and cycle count.

    W17.2 / Pattern 3c: cycle count comes from `roam.quality.cycles` so
    `describe` and `health` always agree on `cycles_total`.
    """
    lines = ["", "## Architecture", ""]
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
        from roam.quality.cycles import cycles_summary

        G = build_symbol_graph(conn)
        if len(G) == 0:
            lines.append("No graph data available.")
            return lines

        layers = detect_layers(G)
        csum = cycles_summary(conn)

        max_layer = max(layers.values()) if layers else 0
        lines.append(f"- **Dependency layers:** {max_layer + 1}")
        lines.append(f"- **Cycles (SCCs):** {csum.total} total, {csum.actionable} actionable")

        if layers:
            layer_counts = Counter(layers.values())
            lines.append(
                "- **Layer distribution:** "
                + ", ".join(f"L{k}: {v} symbols" for k, v in sorted(layer_counts.items())[:5])
            )
    except Exception as exc:
        # W607-K: graph-builder / layer-detection / cycles-summary helper
        # failures surface via warnings_out so the consumer can detect
        # the degrade lineage independent of the markdown blob.
        if warnings_out is not None:
            warnings_out.append(f"describe_architecture_failed:{type(exc).__name__}:{exc}")
        lines.append("Architecture analysis not available (graph module not loaded).")
    return lines


def _section_testing(conn):
    """Test directories, test file count, test-to-source ratio."""
    lines = ["", "## Testing", ""]
    files = conn.execute("SELECT path FROM files").fetchall()
    all_paths = [f["path"].replace("\\", "/") for f in files]

    test_files = []
    source_files = []
    for p in all_paths:
        basename = os.path.basename(p)
        is_test = any(pat in basename for pat in ["test_", "_test.", ".test.", ".spec."]) or any(
            d in p for d in ["tests/", "test/", "__tests__/", "spec/"]
        )
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
                test_dirs.add("/".join(parts[: i + 1]))

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
        except Exception as _exc:  # noqa: BLE001 — defensive
            from roam.observability import log_swallowed

            log_swallowed("cmd_describe:nested", _exc)
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
        except Exception as _exc:  # noqa: BLE001 — defensive
            from roam.observability import log_swallowed

            log_swallowed("cmd_describe:nested", _exc)
        break
    return lines


_DOMAIN_STOP_WORDS = frozenset(
    {
        # Generic programming
        "get",
        "set",
        "new",
        "init",
        "create",
        "make",
        "build",
        "run",
        "test",
        "handle",
        "process",
        "parse",
        "format",
        "check",
        "has",
        "the",
        "from",
        "with",
        "for",
        "add",
        "remove",
        "delete",
        "update",
        "find",
        "load",
        "save",
        "read",
        "write",
        "send",
        "resolve",
        "validate",
        "setup",
        "use",
        "can",
        "should",
        "will",
        "not",
        "all",
        "any",
        "map",
        "list",
        "item",
        "data",
        "type",
        "name",
        "value",
        "key",
        "index",
        "count",
        "result",
        "response",
        "request",
        "error",
        "node",
        "path",
        "base",
        "util",
        "utils",
        "helper",
        "helpers",
        "model",
        "models",
        # UI / framework
        "props",
        "emit",
        "emits",
        "ref",
        "computed",
        "watch",
        "reactive",
        "component",
        "view",
        "render",
        "mount",
        "unmount",
        "click",
        "submit",
        "change",
        "input",
        "select",
        "close",
        "open",
        "show",
        "hide",
        "toggle",
        "modal",
        "form",
        "field",
        "row",
        "col",
        "icon",
        "btn",
        "wrapper",
        "slot",
        "class",
        "style",
        "define",
        "expose",
        "provide",
        "inject",
        # Vue/React/Angular
        "state",
        "store",
        "action",
        "dispatch",
        "reducer",
        "effect",
        "callback",
        "memo",
        "context",
        "hook",
        # DOM events / UI interactions
        "focus",
        "blur",
        "hover",
        "scroll",
        "resize",
        "keydown",
        "keyup",
        "keypress",
        "mousedown",
        "mouseup",
        "mouseover",
        "mouseenter",
        "mouseleave",
        "touchstart",
        "touchend",
        "touchmove",
        "drag",
        "drop",
        "dropdown",
        "popover",
        "tooltip",
        "overlay",
        "collapse",
        "expand",
        "visible",
        "disabled",
        "active",
        "selected",
        "loading",
        "loaded",
        "pending",
        "ready",
        "mounted",
        "updated",
        "before",
        "after",
        "enter",
        "leave",
        "transition",
        "animate",
        "width",
        "height",
        "size",
        "offset",
        "position",
        "layout",
        "container",
        "content",
        "header",
        "footer",
        "sidebar",
        "panel",
        "tab",
        "tabs",
        "menu",
        "nav",
        "link",
        "button",
        "label",
        "text",
        "image",
        "avatar",
        "badge",
        "card",
        "dialog",
        "drawer",
        "divider",
        # Generic code patterns
        "fetch",
        "query",
        "execute",
        "apply",
        "call",
        "bind",
        "start",
        "stop",
        "reset",
        "clear",
        "filter",
        "sort",
        "default",
        "config",
        "options",
        "params",
        "args",
    }
)


def _section_domain(conn):
    """Infer project domain from symbol names and manifest files."""
    import re

    lines = ["", "## Domain Keywords", ""]

    project_root = None
    try:
        from roam.db.connection import find_project_root

        project_root = find_project_root()
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)

    lines.extend(_read_manifest_info(project_root))

    symbols = conn.execute(
        "SELECT name FROM symbols WHERE kind IN ('function', 'class', 'method', 'interface', 'struct') LIMIT 2000"
    ).fetchall()

    word_counts: dict[str, int] = {}
    _split_re = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")
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
    """Document detected coding conventions for agents to follow.

    Delegates to ``roam.commands.conventions_helper.compute_conventions``
    so this section agrees with ``roam conventions``, ``roam understand``,
    ``roam minimap``, and ``roam preflight``.
    """
    lines = ["", "## Coding Conventions", ""]
    lines.append("Follow these conventions when writing code in this project:")
    lines.append("")

    result = compute_conventions(conn)
    by_kind = result["by_kind"]

    for kind, label in [("function", "Functions"), ("class", "Classes"), ("method", "Methods")]:
        info = by_kind.get(kind)
        if not info:
            continue
        if info["pct"] >= 80:
            lines.append(f"- **{label}:** Use `{info['style']}` ({info['pct']}% of {info['total']} {info['label']})")

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
                lines.append(f"- **Imports:** Relative imports common ({100 - pct}% same-directory)")
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)

    # Test patterns
    test_files = conn.execute("SELECT path FROM files WHERE path LIKE '%test%'").fetchall()
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


def _section_complexity_guide(conn, *, warnings_out: list[str] | None = None):
    """Document complexity hotspots to guide refactoring."""
    lines = ["", "## Complexity Hotspots", ""]

    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, AVG(cognitive_complexity) as avg_cc FROM symbol_metrics"
        ).fetchone()
        if not row or row["total"] == 0:
            return lines

        lines.append(f"Average function complexity: {row['avg_cc']:.1f} ({row['total']} functions analyzed)")
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
                lines.append(f"| `{r['name']}` | {r['cognitive_complexity']:.0f} | `{r['path']}:{r['line_start']}` |")
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)
        # W607-K: also surface the marker on warnings_out so the consumer
        # gets the disclosure axis even when ROAM_VERBOSE is off.
        if warnings_out is not None:
            warnings_out.append(f"describe_complexity_failed:{type(_exc).__name__}:{_exc}")

    return lines


def _section_dependencies(conn, *, warnings_out: list[str] | None = None):
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
    except Exception as exc:
        # W607-K: file_edges substrate failure (table missing, JOIN error)
        # surfaces via warnings_out for consumer-side degrade detection.
        if warnings_out is not None:
            warnings_out.append(f"describe_dependencies_failed:{type(exc).__name__}:{exc}")
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


def _agent_prompt_data(conn, *, warnings_out: list[str] | None = None):
    """Gather compact data for --agent-prompt output.

    W607-K: optional ``warnings_out`` accumulator threads DB-shape silent
    paths (find_project_root, top-PageRank, hotspots, cycle-health,
    project-shape) onto the envelope so the consumer can detect a
    degraded agent-prompt build independent of sentinel values like
    ``health=N/A`` (which alone do not distinguish "no cycles" from
    "graph-builder threw"). Complementary to W805-I Pattern-2 silent
    SAFE on empty corpus.
    """
    data = {}

    # -- Project overview ---------------------------------------------
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    files = conn.execute("SELECT language FROM files").fetchall()
    lang_counts = Counter(f["language"] for f in files if f["language"])
    languages = ", ".join(lang for lang, _ in lang_counts.most_common(5))

    # Infer project name from project root directory
    try:
        root = find_project_root()
        project_name = root.name
    except Exception as exc:
        # W607-K: project-root resolution failure is a silent path that
        # collapses to ``"unknown"`` — surface it so the consumer doesn't
        # confuse "no manifest" with "project_root resolver crashed".
        if warnings_out is not None:
            warnings_out.append(f"describe_project_root_failed:{type(exc).__name__}:{exc}")
        project_name = "unknown"

    data["project"] = project_name
    data["files"] = total_files
    data["symbols"] = total_symbols
    data["languages"] = languages

    # -- Stack / key dependencies --------------------------------------
    # Previous implementation extracted top-level directories from the
    # most-imported files and emitted them as "Stack: src" on any
    # monorepo where local imports dominated. The language list above
    # already conveys the same info more accurately, so we no longer
    # emit a Stack line in the agent-prompt text (research note:
    # internal/dogfood/research/roam-describe-stack-directory-leak-2026-05-12.md).
    # JSON consumers that historically read `stack` get an empty
    # string — same as the previous failure-path default — to preserve
    # envelope shape without leaking directory names.
    data["stack"] = ""

    # -- Conventions --------------------------------------------------
    # Delegate to the canonical detector — every command (describe,
    # understand, minimap, preflight, conventions) shares this code path
    # so they all agree on the same codebase.
    _conv = compute_conventions(conn)
    data["conventions"] = short_conventions_string(_conv["by_kind"], min_pct=70) or "mixed"

    # -- Directory structure ------------------------------------------
    dir_rows = conn.execute("""
        SELECT CASE WHEN INSTR(REPLACE(path, '\\', '/'), '/') > 0
               THEN SUBSTR(REPLACE(path, '\\', '/'), 1, INSTR(REPLACE(path, '\\', '/'), '/') - 1)
               ELSE '.' END as dir,
               COUNT(*) as cnt
        FROM files GROUP BY dir ORDER BY cnt DESC
    """).fetchall()
    dir_parts = [f"{r['dir']}/ ({r['cnt']})" for r in dir_rows[:8]]
    data["structure"] = ", ".join(dir_parts)

    # -- Key abstractions (top 5 by PageRank) -------------------------
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
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)
        if warnings_out is not None:
            warnings_out.append(f"describe_key_abstractions_failed:{type(_exc).__name__}:{_exc}")
    data["key_abstractions"] = abstractions

    # -- Hotspots (top 3 by churn * complexity) -----------------------
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
                f"{r['name']} (complexity={r['cognitive_complexity']:.0f}, churn={r['churn']}, {r['path']})"
            )
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)
        if warnings_out is not None:
            warnings_out.append(f"describe_hotspots_failed:{type(_exc).__name__}:{_exc}")
    data["hotspots"] = hotspots

    # -- Cycle-only health estimate + canonical cycle counts --------
    # This is intentionally a *cheap* estimate and NOT the same number
    # `roam health` reports (which weights god components, bottlenecks,
    # and layer violations on top of cycles). Round 4 #17 noted the
    # name collision was misleading agents — both reads are valid, but
    # they measure different things.
    #
    # W17.2 / Pattern 3c: route the cycle-count via `roam.quality.cycles`
    # so `describe`, `health`, and `agent-export` always agree on the
    # actionable + total numbers. `data["cycles"]` keeps reporting the
    # actionable count (legacy), and we add `cycles_actionable` /
    # `cycles_total` + a definition label.
    data["cycle_health_estimate"] = "N/A"
    data["cycles"] = "N/A"
    data["cycles_actionable"] = "N/A"
    data["cycles_total"] = "N/A"
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles, format_cycles, mark_actionable_cycles
        from roam.quality.cycles import cycles_summary
        from roam.quality.cycles import definition as _cyc_def

        G = build_symbol_graph(conn)
        total_syms = len(G)
        if total_syms > 0:
            cycles = find_cycles(G)
            formatted = format_cycles(cycles, conn) if cycles else []
            mark_actionable_cycles(formatted)
            actionable_syms = sum(len(scc) for scc, fc in zip(cycles, formatted) if fc.get("actionable"))
            cycle_pct = (actionable_syms / total_syms * 100) if total_syms else 0
            score = max(0, 100 - int(cycle_pct * 2))
            data["cycle_health_estimate"] = score
            # Use the canonical summary so describe agrees with health.
            csum = cycles_summary(conn)
            data["cycles"] = csum.actionable
            data["cycles_actionable"] = csum.actionable
            data["cycles_total"] = csum.total
            data["cycles_informational"] = csum.informational
            data["cycles_definition"] = _cyc_def()
            # Keep the legacy alias around so existing tooling doesn't break.
            data["health_score"] = score
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_describe:section", _exc)
        if warnings_out is not None:
            warnings_out.append(f"describe_cycle_health_failed:{type(_exc).__name__}:{_exc}")

    # -- Test command guess --
    from roam.output.project_shape import detect_project_shape

    try:
        shape = detect_project_shape(conn, find_project_root())
        if shape.test_command:
            data["test_cmd"] = shape.test_command
            data["test_runner"] = shape.test_runner
        elif shape.test_runner:
            data["test_cmd"] = shape.test_runner
            data["test_runner"] = shape.test_runner
    except Exception as exc:
        # Detector should never fail, but guarantee describe still emits.
        # W607-K: still surface a marker so the consumer can detect the
        # "test_cmd fell back to heuristic" lineage.
        if warnings_out is not None:
            warnings_out.append(f"describe_project_shape_failed:{type(exc).__name__}:{exc}")

    if "test_cmd" not in data:
        # Fallback to the historic heuristic if shape detection turned up nothing.
        test_files = conn.execute("SELECT path FROM files WHERE path LIKE '%test%' LIMIT 1").fetchall()
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
    lines.append(f"Project: {data['project']} ({data['files']} files, {data['symbols']} symbols, {data['languages']})")
    # Stack line removed — the language list already covers what the
    # Stack heuristic tried to surface, and the old detector leaked raw
    # directory names ("Stack: src") on monorepos.
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

    health = data.get("cycle_health_estimate", data.get("health_score", "N/A"))
    cycles = data.get("cycles", "N/A")
    # Disambiguate from `roam health` — the score here is a cheap
    # cycle-only estimate; the full composite lives behind `roam health`.
    lines.append(
        f"Cycle-health estimate: {health}/100, {cycles} actionable cycles (run `roam health` for composite score)"
    )

    if data.get("test_cmd"):
        lines.append(f"Test cmd: {data['test_cmd']}")

    return "\n".join(lines)


# Roam usage instructions prepended to written agent config files.
# Mirrors the "Copy-paste agent instructions" block in the README.
_ROAM_USAGE_INSTRUCTIONS = """\
## Codebase navigation with roam

This project uses `roam` for codebase comprehension. Always prefer roam \
over Glob/Grep/Read exploration.

Before modifying any code:
1. First time in the repo: `roam understand` then `roam tour`
2. Find a symbol: `roam search <pattern>`
3. Before changing a symbol: `roam preflight <name>` (blast radius + tests + fitness)
4. Need files to read: `roam context <name>` (files + line ranges, prioritized)
5. Debugging a failure: `roam diagnose <name>` (root cause ranking)
6. After making changes: `roam diff` (blast radius of uncommitted changes)

Additional commands: `roam health` (0-100 score), `roam impact <name>` (what breaks),
`roam pr-risk` (PR risk score), `roam file <path>` (file skeleton).

Run `roam --help` for all commands. Use `roam --json <cmd>` for structured output.
"""

# Agent config file detection order — first existing file wins.
# If none exist, fall back to CLAUDE.md (most common).
_AGENT_CONFIG_FILES = [
    "CLAUDE.md",  # Claude Code
    "AGENTS.md",  # OpenAI Codex CLI
    "GEMINI.md",  # Gemini CLI
    ".cursor/rules/roam.mdc",  # Cursor
    ".windsurf/rules/roam.md",  # Windsurf
    ".github/copilot-instructions.md",  # GitHub Copilot
    "CONVENTIONS.md",  # Aider / generic
    ".clinerules/roam.md",  # Cline
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


@roam_capability(
    name="describe",
    category="getting-started",
    summary="Auto-generate a project description for AI coding agents",
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
@click.command()
@click.option("--write", is_flag=True, help="Write output to the detected agent config file")
@click.option("--force", is_flag=True, help="Overwrite existing file without confirmation")
@click.option("--agent-prompt", is_flag=True, help="Compact agent-oriented prompt (under 500 tokens)")
@click.option(
    "-o",
    "--output",
    "out_file",
    default=None,
    help="Explicit output path (overrides auto-detection)",
)
@click.pass_context
def describe(ctx, write, force, agent_prompt, out_file):
    """Auto-generate a project description for AI coding agents.

    By default prints to stdout.  Use ``--write`` to save to your agent's
    config file (auto-detected: CLAUDE.md, AGENTS.md, .cursor/rules, etc.)
    or ``-o PATH`` to specify an explicit output path.

    Unlike ``understand`` (which provides a compact codebase overview), this
    command generates a comprehensive multi-section Markdown report with
    ``--write`` to persist directly to CLAUDE.md, AGENTS.md, or .cursor/rules.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    # W607-K: Pattern-2 consumer-layer wiring — thread a ``warnings_out``
    # bucket through the DB-shape section pipeline. cmd_describe is a
    # flagship aggregator that consumes graph_metrics / symbol_metrics /
    # file_edges / build_symbol_graph / cycles_summary substrates; any of
    # those raising silently degrades the markdown blob into a stripped
    # "No X available" sentinel line. The W607-K outer-guard + per-section
    # marker thread makes the degrade lineage visible to consumers
    # independent of the markdown blob. Marker family ``describe_*``
    # (DB scope, distinct from W607-G/H/I/J grep_* / history_* /
    # refs_text_* / delete_check_* subprocess families).
    #
    # Complementary to W805-I strict-xfail Pattern-2 set (which pins the
    # empty-corpus silent-SAFE verdict). W607-K does NOT graduate any
    # W805-I bug — empty-corpus state disclosure is a separate Pattern-2
    # contract orthogonal to the DB-shape degrade axis here.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either ``summary`` or top-level).
    warnings_out: list[str] = []

    # W607-DG: AGGREGATION-PHASE plumbing additive on top of the W607-K
    # substrate-CALL layer. Wraps the 4 aggregation boundaries
    # (``score_classify`` / ``compute_predicate`` / ``compute_verdict`` /
    # ``serialize_envelope``) so a downstream refactor of any of those
    # surfaces a structured marker rather than crashing the envelope or
    # silently misreporting.
    #
    # SYMBOL-EXPLORATION 4-WAY pairing at agg-layer:
    #   cmd_uses    -- W607-U substrate + W607-DE aggregation
    #   cmd_relate  -- W607-W substrate + W607-DA aggregation
    #   cmd_deps    -- W607-V substrate + W607-DB aggregation
    #   cmd_describe-- W607-K substrate + W607-DG aggregation (THIS)
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: every ``default=`` kwarg in a
    # ``_run_check_dg(...)`` call MUST be a literal constant (not a
    # computed expression like ``len(symbols)``). A computed default
    # expression evaluates BEFORE the wrap call, so a raise inside the
    # expression escapes the try-block.
    #
    # W607-K / W607-DG PHASE-NAME COLLISION CHECK (W978 4th-discipline):
    # W607-K substrate phase names (key_abstractions / architecture /
    # complexity / dependencies / project_root / hotspots / cycle_health
    # / project_shape / agent_prompt / pipeline / cycles_summary) do NOT
    # collide with score_classify / compute_predicate / compute_verdict
    # / serialize_envelope, so no rename is required.
    _w607dg_warnings_out: list[str] = []

    def _run_check_dg(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-DG marker emission.

        Mirror of ``_run_check`` shape (same
        ``describe_<phase>_failed:`` marker family) but writes into
        ``_w607dg_warnings_out`` so the additive bucket stays
        distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607dg_warnings_out.append(f"describe_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    if agent_prompt:
        try:
            with open_db(readonly=True) as conn:
                data = _agent_prompt_data(conn, warnings_out=warnings_out)
        except Exception as exc:
            # W607-K outer-guard: the full agent-prompt DB pipeline raised
            # (db corruption / locked / schema drift). Disclose loudly via
            # ``describe_agent_prompt_failed:...`` and fall back to empty
            # data so the envelope still emits cleanly.
            warnings_out.append(f"describe_agent_prompt_failed:{type(exc).__name__}:{exc}")
            data = {
                "project": "unknown",
                "files": 0,
                "symbols": 0,
                "languages": "",
                "stack": "",
                "conventions": "mixed",
                "structure": "",
                "key_abstractions": [],
                "hotspots": [],
                "cycle_health_estimate": "N/A",
                "cycles": "N/A",
                "test_cmd": "",
            }

        # W607-DG -- score_classify boundary (agent-prompt branch). Wraps
        # corpus-shape bucketing into a state label (POPULATED / EMPTY /
        # DEGRADED) so a downstream refactor of the state selection logic
        # surfaces a marker. Floor returns documented "DEGRADED" so
        # downstream serialize_envelope stays non-null.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: ``data`` passed as raw arg;
        # all ``.get()`` calls live INSIDE the closure with literal
        # defaults (cmd_taint W607-CJ 5th-discipline anchor). Floor dict
        # is a literal constant.
        def _score_classify_corpus(_data):
            _files = _data.get("files", 0) if isinstance(_data, dict) else 0
            _symbols = _data.get("symbols", 0) if isinstance(_data, dict) else 0
            if _files == 0 and _symbols == 0:
                _state = "EMPTY"
            elif _files > 0 and _symbols > 0:
                _state = "POPULATED"
            else:
                _state = "PARTIAL"
            return {"state": _state, "file_count": _files, "symbol_count": _symbols}

        _ap_score_dict = _run_check_dg(
            "score_classify",
            _score_classify_corpus,
            data,
            default={"state": "DEGRADED", "file_count": 0, "symbol_count": 0},
        )

        # W607-DG -- compute_predicate boundary (agent-prompt branch).
        # Wraps describe-metrics extraction (file_count + symbol_count +
        # language_count) so a future schema refactor that drops or
        # renames fields on the ``data`` dict surfaces a marker rather
        # than crashing the envelope.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: raw dict passed as arg;
        # ``.get()`` lookups live INSIDE the closure with literal
        # defaults. Floor dict is a literal constant.
        def _compute_predicate_fields_ap(_data) -> dict:
            if not isinstance(_data, dict):
                return {"file_count": 0, "symbol_count": 0, "language_count": 0}
            _files = _data.get("files", 0)
            _symbols = _data.get("symbols", 0)
            _languages_str = _data.get("languages", "") or ""
            # languages may be a comma-separated string -- count parts
            _lang_count = len([p for p in _languages_str.split(",") if p.strip()])
            return {
                "file_count": _files,
                "symbol_count": _symbols,
                "language_count": _lang_count,
            }

        _ap_pred_fields = _run_check_dg(
            "compute_predicate",
            _compute_predicate_fields_ap,
            data,
            default={"file_count": 0, "symbol_count": 0, "language_count": 0},
        )

        # W607-DG -- compute_verdict boundary (agent-prompt branch).
        # Wraps the verdict-string assembly so a downstream f-string
        # refactor (non-numeric values from a vocabulary refactor, or a
        # __format__-raising sentinel) surfaces a marker rather than
        # crashing the envelope. Floor must NOT re-interpolate the same
        # values that tripped the closure (W978 first-hypothesis). Use
        # the literal "describe analysis completed" floor.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: raw dict passed as arg;
        # ``.get()`` lookups + f-string interpolation live INSIDE the
        # closure. Floor is a literal string constant.
        def _build_ap_verdict_str(_data):
            return (
                f"{_data.get('project', 'project')}: "
                f"{_data.get('files', 0)} files, "
                f"{_data.get('languages', 'unknown')} | "
                f"health={_data.get('health_score', 'N/A')}"
            )

        _ap_verdict = _run_check_dg(
            "compute_verdict",
            _build_ap_verdict_str,
            data,
            default="describe analysis completed",
        )

        if json_mode:
            ap_summary: dict[str, object] = {
                "verdict": _ap_verdict,
                "mode": "agent-prompt",
                # W607-DG: surface score_classify state + predicate
                # metrics on the envelope so consumers can read the
                # corpus shape without re-deriving from ``data``.
                "corpus_state": _ap_score_dict["state"],
                "file_count": _ap_pred_fields["file_count"],
                "symbol_count": _ap_pred_fields["symbol_count"],
                "language_count": _ap_pred_fields["language_count"],
            }
            # W607-K + W607-DG: combined warnings_out at envelope-emit
            # time so consumers see the full degradation lineage in
            # marker-emission order (substrate-CALL + aggregation-phase
            # share the same ``describe_*`` family).
            _ap_combined_wo = list(warnings_out) + list(_w607dg_warnings_out)
            if _ap_combined_wo:
                # W607-K: surface marker bucket on summary mirror so the
                # consumer sees the substrate-degrade lineage. We do NOT
                # flip ``partial_success`` on the agent-prompt branch
                # here — that flip is what W805-I's strict-xfail
                # ``test_agent_prompt_empty_corpus_partial_success_coupled_to_na``
                # is pinning as a SEPARATE Pattern-2 fix wave (the
                # health=N/A + partial_success=False mismatch). W607-K
                # adds the marker disclosure only; W805-I will graduate
                # the partial_success contract on its own wave.
                # W607-DG: aggregation-phase markers DO flip
                # partial_success (distinct contract from substrate-only
                # W607-K markers).
                ap_summary["warnings_out"] = list(_ap_combined_wo)
                if _w607dg_warnings_out:
                    ap_summary["partial_success"] = True

            # W607-DG -- serialize_envelope boundary. Wraps the envelope
            # serialization itself. A downstream schema-shape refactor
            # that breaks ``json_envelope("describe", ...)`` would
            # otherwise crash AFTER all substrate + aggregation signals
            # were already gathered. Floor to a minimal envelope stub so
            # consumers still receive a parseable JSON object with the
            # marker attached + the canonical command name.
            _ap_kwargs: dict = {
                "summary": ap_summary,
                **data,
            }
            if _ap_combined_wo:
                _ap_kwargs["warnings_out"] = list(_ap_combined_wo)
            _ap_envelope_floor: dict = {
                "command": "describe",
                "schema_version": "1.0.0",
                "summary": {
                    "verdict": _ap_verdict,
                    "partial_success": True,
                    "warnings_out": list(_ap_combined_wo),
                },
                "warnings_out": list(_ap_combined_wo),
            }
            _ap_envelope = _run_check_dg(
                "serialize_envelope",
                json_envelope,
                "describe",
                default=_ap_envelope_floor,
                **_ap_kwargs,
            )
            # W607-DG -- if ``serialize_envelope`` raised AFTER the
            # combined bucket was already snapshotted, the new
            # ``describe_serialize_envelope_failed:`` marker was appended
            # to ``_w607dg_warnings_out`` and the floor stub carries only
            # the pre-raise combined list. Rebuild the floor stub's
            # warnings_out so the new marker reaches the JSON output.
            if _ap_envelope is _ap_envelope_floor and _w607dg_warnings_out:
                _ap_combined_wo = list(warnings_out) + list(_w607dg_warnings_out)
                _ap_envelope_floor["summary"]["warnings_out"] = list(_ap_combined_wo)
                _ap_envelope_floor["warnings_out"] = list(_ap_combined_wo)
                _ap_envelope = _ap_envelope_floor
            click.echo(to_json(_ap_envelope))
        else:
            click.echo(f"VERDICT: {_ap_verdict}")
            click.echo()
            click.echo(_format_agent_prompt(data))
        return

    try:
        with open_db(readonly=True) as conn:
            sections = []
            sections.append(["# Project Architecture", ""])
            sections.append(_section_overview(conn))
            sections.append(_section_directories(conn))
            sections.append(_section_entry_points(conn))
            sections.append(_section_key_abstractions(conn, warnings_out=warnings_out))
            sections.append(_section_architecture(conn, warnings_out=warnings_out))
            sections.append(_section_testing(conn))
            sections.append(_section_conventions(conn))
            sections.append(_section_complexity_guide(conn, warnings_out=warnings_out))
            sections.append(_section_domain(conn))
            sections.append(_section_dependencies(conn, warnings_out=warnings_out))

            output = "\n".join(line for sec in sections for line in sec)

            # Gather compact verdict data
            _total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            _lang_counts = Counter(
                f["language"] for f in conn.execute("SELECT language FROM files").fetchall() if f["language"]
            )
            _top_lang = _lang_counts.most_common(1)[0][0] if _lang_counts else "unknown"
            _n_langs = len(_lang_counts)
    except Exception as exc:
        # W607-K outer-guard: the full section pipeline raised. Disclose
        # via ``describe_pipeline_failed:...`` + fall back to empty
        # verdict floors so the envelope still emits.
        warnings_out.append(f"describe_pipeline_failed:{type(exc).__name__}:{exc}")
        output = "# Project Architecture\n"
        _total_files = 0
        _top_lang = "unknown"
        _n_langs = 0

    # W607-DG -- compute_verdict boundary (main describe branch). Wraps
    # the verdict-string assembly so a downstream f-string refactor
    # (non-numeric values from a vocabulary refactor, or a
    # __format__-raising sentinel) surfaces a marker rather than
    # crashing the envelope. Floor must NOT re-interpolate the same
    # values that tripped the closure (W978 first-hypothesis). Use the
    # literal "describe analysis completed" floor (LAW 6 standalone-
    # parse).
    #
    # W978 KWARG-DEFAULT EAGERNESS TRAP: raw scalars passed as args;
    # f-string interpolation lives INSIDE the closure. Floor is a
    # literal string constant.
    def _build_desc_verdict_str(_top_lang_in, _total_files_in, _n_langs_in):
        return f"{_top_lang_in} project, {_total_files_in} files, {_n_langs_in} languages"

    _desc_verdict = _run_check_dg(
        "compute_verdict",
        _build_desc_verdict_str,
        _top_lang,
        _total_files,
        _n_langs,
        default="describe analysis completed",
    )

    if json_mode:
        # W17.2 / Pattern 3c: include canonical cycle metric definition
        # so the describe envelope agrees with health/agent-export on
        # what "cycles" means even when only the markdown blob is read.
        from roam.quality.cycles import (
            cycles_summary as _cs,
        )
        from roam.quality.cycles import (
            definition as _cyc_def,
        )

        try:
            with open_db(readonly=True) as _c2:
                _csum = _cs(_c2)
            _cycles_total = _csum.total
            _cycles_actionable = _csum.actionable
        except Exception as exc:
            # W607-K: cycles_summary substrate failure on the JSON tail
            # path. Surface via warnings_out + fall back to 0 floors.
            warnings_out.append(f"describe_cycles_summary_failed:{type(exc).__name__}:{exc}")
            _cycles_total = 0
            _cycles_actionable = 0

        # W607-DG -- score_classify boundary (main describe branch).
        # Wraps corpus-shape bucketing into a state label (POPULATED /
        # EMPTY / DEGRADED) so a downstream refactor of the state
        # selection logic surfaces a marker. Floor returns documented
        # "DEGRADED" so downstream serialize_envelope stays non-null.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: raw scalars passed as args;
        # all comparisons live INSIDE the closure. Floor dict is a
        # literal constant.
        def _score_classify_main(_total_files_in, _n_langs_in, _cycles_total_in):
            if _total_files_in == 0 and _n_langs_in == 0:
                _state = "EMPTY"
            elif _cycles_total_in > 0:
                _state = "CYCLES_PRESENT"
            else:
                _state = "POPULATED"
            return {
                "state": _state,
                "file_count": _total_files_in,
                "language_count": _n_langs_in,
            }

        _desc_score_dict = _run_check_dg(
            "score_classify",
            _score_classify_main,
            _total_files,
            _n_langs,
            _cycles_total,
            default={"state": "DEGRADED", "file_count": 0, "language_count": 0},
        )

        # W607-DG -- compute_predicate boundary (main describe branch).
        # Wraps the describe-metrics extraction (markdown_length +
        # file_count + language_count) so a future refactor that
        # changes the metric shape surfaces a marker rather than
        # crashing the envelope.
        #
        # W978 KWARG-DEFAULT EAGERNESS TRAP: raw scalars + raw string
        # passed as args; ``len()`` lives INSIDE the closure (cmd_taint
        # W607-CJ 5th-discipline anchor). Floor dict is a literal
        # constant.
        def _compute_predicate_fields_main(_output_in, _total_files_in, _n_langs_in, _cycles_total_in) -> dict:
            _markdown_length = len(_output_in) if _output_in is not None else 0
            return {
                "markdown_length": _markdown_length,
                "file_count": _total_files_in,
                "language_count": _n_langs_in,
                "cycles_total": _cycles_total_in,
            }

        _desc_pred_fields = _run_check_dg(
            "compute_predicate",
            _compute_predicate_fields_main,
            output,
            _total_files,
            _n_langs,
            _cycles_total,
            default={
                "markdown_length": 0,
                "file_count": 0,
                "language_count": 0,
                "cycles_total": 0,
            },
        )

        # W978 KWARG-DEFAULT EAGERNESS NOTE (W607-CR 7th-discipline
        # anchor): do NOT use ``_desc_pred_fields.get("markdown_length",
        # len(output))`` -- the second arg evaluates EAGERLY.
        # _desc_pred_fields ALWAYS carries the keys (either real value
        # or floor 0), so a bare lookup is correct.
        desc_summary: dict[str, object] = {
            "verdict": _desc_verdict,
            "length": _desc_pred_fields["markdown_length"],
            "cycles_total": _cycles_total,
            "cycles_actionable": _cycles_actionable,
            "cycles_definition": _cyc_def(),
            # W607-DG: surface score_classify state + predicate metrics
            # so consumers can read the corpus shape without re-deriving
            # from raw markdown.
            "corpus_state": _desc_score_dict["state"],
            "file_count": _desc_pred_fields["file_count"],
            "language_count": _desc_pred_fields["language_count"],
        }

        # W607-K + W607-DG: combined warnings_out at envelope-emit time
        # so consumers see the full degradation lineage in marker-
        # emission order (substrate-CALL + aggregation-phase share the
        # same ``describe_*`` family). ``partial_success`` flips if
        # EITHER bucket carries markers (mirror of W607-DA / W607-DB
        # contract).
        _desc_combined_wo = list(warnings_out) + list(_w607dg_warnings_out)
        if _desc_combined_wo:
            desc_summary["warnings_out"] = list(_desc_combined_wo)
            desc_summary["partial_success"] = True

        # W607-DG -- serialize_envelope boundary. Wraps the envelope
        # serialization itself. A downstream schema-shape refactor that
        # breaks ``json_envelope("describe", ...)`` would otherwise
        # crash AFTER all substrate + aggregation signals were already
        # gathered. Floor to a minimal envelope stub so consumers still
        # receive a parseable JSON object with the marker attached + the
        # canonical command name.
        _desc_kwargs: dict = {
            "summary": desc_summary,
            "markdown": output,
        }
        if _desc_combined_wo:
            _desc_kwargs["warnings_out"] = list(_desc_combined_wo)
        _desc_envelope_floor: dict = {
            "command": "describe",
            "schema_version": "1.0.0",
            "summary": {
                "verdict": _desc_verdict,
                "partial_success": True,
                "warnings_out": list(_desc_combined_wo),
            },
            "warnings_out": list(_desc_combined_wo),
        }
        _desc_envelope = _run_check_dg(
            "serialize_envelope",
            json_envelope,
            "describe",
            default=_desc_envelope_floor,
            **_desc_kwargs,
        )
        # W607-DG -- if ``serialize_envelope`` raised AFTER the combined
        # bucket was already snapshotted, the new
        # ``describe_serialize_envelope_failed:`` marker was appended to
        # ``_w607dg_warnings_out`` and the floor stub carries only the
        # pre-raise combined list. Rebuild the floor stub's
        # warnings_out so the new marker reaches the JSON output.
        if _desc_envelope is _desc_envelope_floor and _w607dg_warnings_out:
            _desc_combined_wo = list(warnings_out) + list(_w607dg_warnings_out)
            _desc_envelope_floor["summary"]["warnings_out"] = list(_desc_combined_wo)
            _desc_envelope_floor["warnings_out"] = list(_desc_combined_wo)
            _desc_envelope = _desc_envelope_floor
        click.echo(to_json(_desc_envelope))
        return

    click.echo(f"VERDICT: {_desc_verdict}")
    click.echo()

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
        # Prepend roam usage instructions so AI agents know how to use roam
        write_output = _ROAM_USAGE_INSTRUCTIONS + "\n" + output
        out_path.write_text(write_output, encoding="utf-8")
        click.echo(f"Wrote {out_path}")
    else:
        click.echo(output)
