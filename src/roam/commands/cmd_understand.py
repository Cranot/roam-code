"""Single-call codebase comprehension — everything an AI agent needs in one shot.

Naming-conventions detection delegates to the canonical helper in
``roam.commands.conventions_helper`` so this command, ``roam describe``,
``roam minimap``, ``roam preflight``, and ``roam conventions`` all agree
on the same codebase.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because understand outputs are invocation-scoped comprehensive
architecture envelopes — not per-location violations. Underlying
detectors (health, smells, vibe-check) emit their own SARIF where it
fits. See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket
B propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import fnmatch
import re
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.changed_files import is_test_file
from roam.commands.conventions_helper import compute_conventions
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.db.edge_kinds import inheritance_in_clause
from roam.output.formatter import abbrev_kind, json_envelope, loc, resolution_disclosure, to_json
from roam.output.framework_filter import is_framework_alias
from roam.quality.health_band import definition as health_band_definition
from roam.quality.health_band import health_band

# ---------------------------------------------------------------------------
# Framework / build-tool detection
# ---------------------------------------------------------------------------

_FRAMEWORK_PATTERNS = {
    # JS/TS frameworks
    "vue": (["vue", "@vue"], ["*.vue"]),
    "react": (["react", "react-dom", "@react"], []),
    "angular": (["@angular/core", "@angular"], []),
    "svelte": (["svelte", "@sveltejs"], ["*.svelte"]),
    "next.js": (["next"], ["next.config.*"]),
    "nuxt": (["nuxt", "@nuxt"], ["nuxt.config.*"]),
    # State management
    "pinia": (["pinia"], []),
    "vuex": (["vuex"], []),
    "redux": (["redux", "@reduxjs/toolkit"], []),
    # CSS
    "tailwind": (["tailwindcss"], ["tailwind.config.*"]),
    # Python
    "django": (["django"], []),
    "flask": (["flask"], []),
    "fastapi": (["fastapi"], []),
    # Go
    "gin": (["github.com/gin-gonic/gin"], []),
    "fiber": (["github.com/gofiber/fiber"], []),
    # Rust
    "actix": (["actix-web"], []),
    "axum": (["axum"], []),
    # .NET / C#
    "asp.net": (["microsoft.aspnetcore"], []),
    "entity-framework": (["microsoft.entityframeworkcore"], []),
    "blazor": (["microsoft.aspnetcore.components"], []),
    "wpf": (["system.windows"], []),
    "winforms": (["system.windows.forms"], []),
    "xamarin": (["xamarin.forms", "xamarin.essentials"], []),
}

_BUILD_PATTERNS = {
    "vite": ["vite.config.*"],
    "webpack": ["webpack.config.*"],
    "rollup": ["rollup.config.*"],
    "esbuild": ["esbuild.*"],
    "turbopack": ["turbo.json"],
    "cargo": ["Cargo.toml"],
    "go": ["go.mod"],
    "maven": ["pom.xml"],
    "gradle": ["build.gradle*"],
    "pip": ["pyproject.toml", "setup.py", "setup.cfg"],
    "composer": ["composer.json"],
    "dotnet": ["*.csproj", "*.sln", "*.fsproj", "*.vbproj"],
}


def _detect_frameworks(conn):
    """Detect frameworks by scanning edge targets, file names, and source content."""
    # collect unique edge target names from resolved references
    import_targets = set()
    for r in conn.execute("SELECT DISTINCT s.name FROM symbols s JOIN edges e ON e.target_id = s.id").fetchall():
        import_targets.add(r["name"].lower())

    # scan a sample of source files for import/using statements that reference
    # external packages (these won't appear in resolved edges since the
    # framework symbols aren't in the local codebase)
    _IMPORT_RE = re.compile(
        r"\busing\s+([\w.]+)"  # C#: using Microsoft.AspNetCore.Mvc;
        r'|\bfrom\s+[\'"]([^"\']+)[\'"]'  # JS/TS: from 'next/router'
        r"|\bimport\s+([\w.]+)"  # Python/Go: import x
        r"|\bfrom\s+([\w.]+)\s+import"  # Python: from x import y
    )
    # Restrict the scan to actual source-language files. Markdown / YAML /
    # JSON / TOML often quote ``from django import ...`` inside documentation,
    # rules, or changelog prose — counting those as project imports turns
    # roam-on-roam into a false-positive react/django/flask/asp.net detection.
    # Note: tree-sitter language ids use ``c_sharp`` not ``csharp``;
    # mirror src/roam/languages/registry.py:_SUPPORTED_LANGUAGES.
    _SOURCE_LANGUAGES = {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "jsx",
        "java",
        "go",
        "rust",
        "c",
        "cpp",
        "c_sharp",
        "php",
        "ruby",
        "kotlin",
        "swift",
        "scala",
        "sql",
        "dart",
        "vue",
        "svelte",
        "apex",
        "aura",
        "visualforce",
        "sfxml",
        "hcl",
        "foxpro",
    }
    # Non-source directories that legitimately *describe* frameworks without
    # *being* a user of them.
    _DOC_DIRS = ("rules/", "docs/", "dev/", "internal/", "benchmarks/", "tests/")
    root = find_project_root()
    for r in conn.execute(
        "SELECT path, language FROM files WHERE language IS NOT NULL ORDER BY path LIMIT 200"
    ).fetchall():
        lang = (r["language"] or "").lower()
        if lang not in _SOURCE_LANGUAGES:
            continue
        norm_path = r["path"].replace("\\", "/").lower()
        if any(norm_path.startswith(d) or ("/" + d) in norm_path for d in _DOC_DIRS):
            continue
        file_path = root / r["path"]
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore").lower()
            for match in _IMPORT_RE.finditer(content):
                # groups are mutually exclusive; pick the one that matched
                val = match.group(1) or match.group(2) or match.group(3) or match.group(4)
                if val:
                    import_targets.add(val)
        except Exception as _exc:  # noqa: BLE001 — defensive
            from roam.observability import log_swallowed

            log_swallowed("cmd_understand:import_scan", _exc)

    # collect file paths for pattern matching
    file_paths = set()
    for r in conn.execute("SELECT path FROM files").fetchall():
        file_paths.add(r["path"].replace("\\", "/").lower())

    detected = []
    for name, (import_pats, file_pats) in _FRAMEWORK_PATTERNS.items():
        found = False
        for pat in import_pats:
            if _matches_import_pattern(pat.lower(), import_targets):
                found = True
                break
        if not found:
            for pat in file_pats:
                if any(fnmatch.fnmatch(fp.split("/")[-1], pat.lower()) for fp in file_paths):
                    found = True
                    break
        if found:
            detected.append(name)

    return detected


def _matches_import_pattern(pattern: str, targets: set) -> bool:
    """check if pattern matches any target as a prefix or path segment.

    examples:
      - pattern "next" matches "next" or "next/router" but NOT "getnextpage"
      - pattern "react" matches "react" or "react-dom" but NOT "somereactiveext"
      - pattern "microsoft.aspnetcore" matches "microsoft.aspnetcore.mvc"
    """
    for target in targets:
        # exact match
        if target == pattern:
            return True
        # prefix match with delimiter (/, ., -, @)
        # handles: "next/router", "react-dom", "@angular/core", "microsoft.aspnetcore.mvc"
        if target.startswith(pattern) and len(target) > len(pattern):
            next_char = target[len(pattern)]
            if next_char in (".", "/", "-", "@"):
                return True
    return False


def _detect_build(conn):
    """Detect build tool from file names."""
    file_names = set()
    for r in conn.execute("SELECT path FROM files").fetchall():
        name = r["path"].replace("\\", "/").split("/")[-1].lower()
        file_names.add(name)

    for tool, patterns in _BUILD_PATTERNS.items():
        for pat in patterns:
            if any(fnmatch.fnmatch(fn, pat.lower()) for fn in file_names):
                return tool
    return None


# ---------------------------------------------------------------------------
# Key abstractions: top symbols by PageRank + fan analysis
# ---------------------------------------------------------------------------


def _key_abstractions(conn, limit=15):
    """Find the most important symbols by PageRank with fan analysis.

    Pulls a wider set than ``limit`` so the framework-alias filter
    drops noise (Vue ``computed<T>``, React ``useState<T>``, etc.) and
    test-fixture filter drops pytest-conftest pollution while still
    returning ``limit`` real abstractions to the caller.

    v12.12.5: skip symbols whose file is classified as ``test``.
    pytest fixtures (``cli_runner``, ``indexed_project``, …) inflate
    PageRank because every test imports them, so they outrank real
    abstractions like ``cli`` / ``open_db`` / ``json_envelope``. They
    aren't useful as "key abstractions" for understanding the project.
    """
    rows = conn.execute(
        "SELECT s.name, s.qualified_name, s.kind, f.path as file_path, "
        "s.line_start, gm.pagerank, gm.in_degree, gm.out_degree, "
        "COALESCE(f.file_role, 'source') AS file_role "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "JOIN graph_metrics gm ON s.id = gm.symbol_id "
        "WHERE s.kind IN ('function', 'class', 'method', 'interface') "
        "AND s.is_exported = 1 "
        "ORDER BY gm.pagerank DESC LIMIT ?",
        (limit * 6,),
    ).fetchall()
    filtered = []
    for r in rows:
        if r["file_role"] == "test":
            continue
        if is_framework_alias(r["qualified_name"] or r["name"], r["kind"], r["file_path"]):
            continue
        filtered.append(r)
    rows = filtered[:limit]

    results = []
    for r in rows:
        fan_in = r["in_degree"] or 0
        fan_out = r["out_degree"] or 0

        # Why is this important?
        if fan_in > 20:
            why = f"highly imported ({fan_in} dependents)"
        elif fan_in > 10:
            why = f"widely used ({fan_in} dependents)"
        elif r["kind"] == "class":
            why = "core class"
        else:
            why = "high PageRank"

        results.append(
            {
                "name": r["qualified_name"] or r["name"],
                "kind": r["kind"],
                "location": loc(r["file_path"], r["line_start"]),
                "pagerank": round(r["pagerank"] or 0, 4),
                "fan_in": fan_in,
                "fan_out": fan_out,
                "why": why,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Entry points: files with no importers + high PageRank
# ---------------------------------------------------------------------------


def _find_entry_points(conn, limit=10):
    """Find likely entry point files (no importers + have symbols).

    v12.12.5: exclude tests, dev scripts, generated code, and other
    non-source roles. Test files have no importers in the source
    graph (nothing depends on a test file) so they kept polluting
    the "Entry points" list — every newcomer running ``roam understand``
    saw "tests/test_comprehensive.py" listed as an entry point, which
    is exactly wrong for orientation.
    """
    rows = conn.execute(
        "SELECT f.id, f.path, f.language, COUNT(s.id) as sym_count, "
        "       COALESCE(f.file_role, 'source') AS file_role "
        "FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "WHERE f.id NOT IN (SELECT DISTINCT target_file_id FROM file_edges) "
        "AND COALESCE(f.file_role, 'source') NOT IN ("
        "  'test', 'scripts', 'generated', 'vendored', 'data', "
        "  'examples', 'build', 'ci', 'docs', 'config'"
        ") "
        "GROUP BY f.id "
        "HAVING sym_count > 0 "
        "ORDER BY sym_count DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()

    return [{"path": r["path"], "symbols": r["sym_count"]} for r in rows]


# ---------------------------------------------------------------------------
# Hotspots: churn * coupling
# ---------------------------------------------------------------------------


_DOC_EXTS_HOTSPOTS = frozenset({".md", ".rst", ".mdx", ".adoc", ".txt"})
_CONFIG_EXTS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".properties", ".conf"})
_SQL_EXTS = frozenset({".sql", ".ddl"})
_CODE_EXTS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".scala",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".lua",
        ".dart",
        ".ex",
        ".exs",
        ".erl",
        ".elm",
        ".clj",
        ".cljs",
        ".m",
        ".mm",
        ".f",
        ".f90",
        ".f95",
        ".prg",
        ".cls",
        ".trg",
        ".page",
        ".cob",
        ".cobol",
        ".pas",
        ".scx",
    }
)


def _hotspot_kind(path: str) -> str:
    """Classify a churn-hotspot file so doc/config/sql aren't treated as
    code debt. Used by ``roam understand`` to keep README and changelog
    files from dominating the hotspot list."""
    import os as _os

    base = _os.path.basename(path or "").lower()
    ext = _os.path.splitext(base)[1]
    # Dotfile config (.env, .dockerignore, .npmrc, …) — splitext gives
    # ext=".env" and base=".env"; treat the whole basename as the ext.
    if not ext and base.startswith("."):
        ext = base
    if ext in _DOC_EXTS_HOTSPOTS:
        return "doc"
    if ext in _CONFIG_EXTS:
        return "config"
    if ext in _SQL_EXTS:
        return "sql"
    if ext in _CODE_EXTS:
        return "code"
    return "other"


def _find_hotspots(conn, limit=10):
    """Find files with highest churn, annotated with coupling info and kind."""
    # Pull a wider set than the requested limit so we have headroom to
    # rebalance after kind-classification — code hotspots are more
    # actionable than doc hotspots and should dominate the headline.
    rows = conn.execute(
        "SELECT fs.file_id, f.path, fs.total_churn, fs.commit_count, "
        "fs.distinct_authors "
        "FROM file_stats fs "
        "JOIN files f ON fs.file_id = f.id "
        "WHERE fs.total_churn > 0 "
        "ORDER BY fs.total_churn DESC "
        "LIMIT ?",
        (limit * 4,),
    ).fetchall()

    code_hits: list[dict] = []
    other_hits: list[dict] = []
    for r in rows:
        if is_test_file(r["path"]):
            continue
        # Count coupling partners
        partners = conn.execute(
            "SELECT COUNT(*) FROM git_cochange WHERE file_id_a = ? OR file_id_b = ?",
            (r["file_id"], r["file_id"]),
        ).fetchone()[0]

        kind = _hotspot_kind(r["path"])
        entry = {
            "path": r["path"],
            "churn": r["total_churn"],
            "commits": r["commit_count"],
            "authors": r["distinct_authors"],
            "coupling_partners": partners,
            "kind": kind,
        }
        if kind == "code":
            code_hits.append(entry)
        else:
            other_hits.append(entry)

    # Code hotspots first (truncated to the requested limit), then any
    # remaining slots filled from doc/config/sql so callers still see them.
    take_code = min(len(code_hits), limit)
    remaining = limit - take_code
    return code_hits[:take_code] + other_hits[:remaining]


# ---------------------------------------------------------------------------
# Suggested reading order for AI agents
# ---------------------------------------------------------------------------


def _suggest_reading_order(conn, entry_points, key_abstractions, hotspots):
    """Build a prioritized reading order for an AI agent exploring the codebase."""
    order = []
    seen = set()
    priority = 1

    # 1. Entry points first
    for ep in entry_points[:3]:
        if ep["path"] not in seen:
            seen.add(ep["path"])
            order.append(
                {
                    "path": ep["path"],
                    "reason": "entry point",
                    "priority": priority,
                }
            )
            priority += 1

    # 2. Files with key abstractions
    for ka in key_abstractions[:5]:
        path = ka["location"].rsplit(":", 1)[0]
        if path not in seen:
            seen.add(path)
            order.append(
                {
                    "path": path,
                    "reason": f"key abstraction ({ka['name']})",
                    "priority": priority,
                }
            )
            priority += 1

    # 3. Hotspots
    for hs in hotspots[:3]:
        if hs["path"] not in seen:
            seen.add(hs["path"])
            order.append(
                {
                    "path": hs["path"],
                    "reason": "active hotspot",
                    "priority": priority,
                }
            )
            priority += 1

    return order


# ---------------------------------------------------------------------------
# Conventions summary (lightweight inline detection)
# ---------------------------------------------------------------------------


def _detect_conventions(conn):
    """Detect dominant naming conventions per symbol kind.

    Thin wrapper around the canonical detector in
    ``roam.commands.conventions_helper`` — returns the per-kind summary
    in the historic shape (``{kind: {style, pct, total}}``) so the
    existing JSON envelope and text renderers stay backward-compatible.
    """
    result = compute_conventions(conn)
    return {
        kind: {
            "style": info["style"],
            "pct": info["pct"],
            "total": info["total"],
        }
        for kind, info in result["by_kind"].items()
        if kind in ("function", "class", "method", "variable")
    }


# ---------------------------------------------------------------------------
# Complexity overview
# ---------------------------------------------------------------------------


def _complexity_overview(conn):
    """Get aggregate complexity stats from symbol_metrics."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "AVG(cognitive_complexity) as avg_cc, "
            "MAX(cognitive_complexity) as max_cc "
            "FROM symbol_metrics"
        ).fetchone()
        if not row or row["total"] == 0:
            return None

        critical = conn.execute("SELECT COUNT(*) FROM symbol_metrics WHERE cognitive_complexity >= 25").fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM symbol_metrics WHERE cognitive_complexity >= 15 AND cognitive_complexity < 25"
        ).fetchone()[0]

        # Top 3 worst
        worst = conn.execute(
            "SELECT s.name, sm.cognitive_complexity, f.path "
            "FROM symbol_metrics sm "
            "JOIN symbols s ON sm.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "ORDER BY sm.cognitive_complexity DESC LIMIT 3"
        ).fetchall()

        return {
            "total_analyzed": row["total"],
            "avg": round(row["avg_cc"] or 0, 1),
            "max": round(row["max_cc"] or 0, 0),
            "critical": critical,
            "high": high,
            "worst": [{"name": w["name"], "cc": round(w["cognitive_complexity"]), "file": w["path"]} for w in worst],
        }
    except sqlite3.OperationalError:
        return None


# ---------------------------------------------------------------------------
# Pattern summary
# ---------------------------------------------------------------------------


def _detect_patterns_summary(conn):
    """Quick lightweight pattern detection (strategy, factory)."""
    patterns = []

    # Strategy: classes sharing a parent.
    # W543-followup: previously filtered on bare ``e.kind = 'inherits'``
    # which silently dropped ``implements`` / ``uses_trait`` rows that
    # the canonical writers emit. Source the IN-clause from the shared
    # helper so future writer additions reach this detector too.
    try:
        rows = conn.execute(
            "SELECT p.name as parent, COUNT(*) as impl_count "
            "FROM symbols s "
            "JOIN edges e ON e.source_id = s.id "
            "JOIN symbols p ON e.target_id = p.id "
            f"WHERE {inheritance_in_clause('e.kind')} "
            "AND p.kind IN ('class', 'interface') "
            "GROUP BY p.name "
            "HAVING COUNT(*) >= 3 "
            "ORDER BY COUNT(*) DESC LIMIT 5"
        ).fetchall()
        for r in rows:
            patterns.append(
                {
                    "type": "strategy/hierarchy",
                    "name": r["parent"],
                    "count": r["impl_count"],
                }
            )
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_understand", _exc)

    # Factory: functions named create_*/build_*/make_*
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM symbols "
            "WHERE kind = 'function' AND "
            "(name LIKE 'create\\_%' ESCAPE '\\' OR name LIKE 'build\\_%' ESCAPE '\\' OR name LIKE 'make\\_%' ESCAPE '\\' OR name LIKE '%Factory%')"
        ).fetchone()[0]
        if count > 0:
            patterns.append({"type": "factory", "name": "factory functions", "count": count})
    except Exception as _exc:  # noqa: BLE001 — defensive
        from roam.observability import log_swallowed

        log_swallowed("cmd_understand", _exc)

    return patterns


# ---------------------------------------------------------------------------
# Debt hotspots
# ---------------------------------------------------------------------------


def _top_debt(conn, limit=5):
    """Compute top debt files (simplified hotspot-weighted)."""
    try:
        rows = conn.execute(
            "SELECT f.path, fs.complexity, fs.total_churn "
            "FROM file_stats fs "
            "JOIN files f ON fs.file_id = f.id "
            "WHERE fs.total_churn > 0 AND fs.complexity > 0 "
            "ORDER BY fs.complexity * fs.total_churn DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "path": r["path"],
                "complexity": round(r["complexity"] or 0, 1),
                "churn": r["total_churn"] or 0,
                "debt_score": round((r["complexity"] or 0) * (r["total_churn"] or 0) / 100, 1),
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@roam_capability(
    name="understand",
    category="exploration",
    summary="Single-call codebase comprehension: structure, stack, hotspots, reading order.",
    inputs=["repo_path"],
    outputs=["structure", "stack", "architecture", "hotspots", "verdict"],
    examples=["roam understand", "roam understand --agent", "roam understand --tour"],
    tags=["overview", "onboarding"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
)
@click.command()
@click.option("--full", is_flag=True, help="Show all clusters and hotspots, not just top-N")
@click.option("--tour", "tour_mode", is_flag=True, help="Append tour: reading order, entry points, next steps")
@click.option("--mermaid", "mermaid_mode", is_flag=True, help="Output Mermaid diagram (only active with --tour)")
@click.option("--agent", "agent_mode", is_flag=True, help="Compact agent-oriented prompt (skips normal output)")
@click.option(
    "--skeleton",
    "skeleton_dir",
    default=None,
    metavar="DIR",
    help="Show structural skeleton of a directory (skips normal output)",
)
@click.pass_context
def understand(ctx, full, tour_mode, mermaid_mode, agent_mode, skeleton_dir):
    """Single-call codebase comprehension — everything in one shot.

    Returns project structure, tech stack, architecture, health, hotspots,
    and a suggested reading order. Designed for AI agents.

    Use --agent for a compact token-efficient prompt block suitable for an AI
    system prompt, --skeleton DIR to see the exported API of a directory, or
    --tour to append a guided reading order and entry points section.

    \b
    Examples:
      roam understand
      roam understand --agent           # compact prompt block
      roam understand --skeleton src/   # exported API of a dir
      roam understand --tour            # add reading order

    See also ``context`` (drill into a specific symbol after orientation),
    ``health`` (quality scores), and ``ask`` (free-form intent dispatch).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    cmd_name = ctx.info_name or "understand"
    ensure_index()

    # ------------------------------------------------------------------
    # Flag: --agent  (compact agent prompt, skips normal output)
    # ------------------------------------------------------------------
    if agent_mode:
        _run_agent_mode(json_mode, cmd_name, token_budget)
        return

    # ------------------------------------------------------------------
    # Flag: --skeleton DIR  (directory skeleton, skips normal output)
    # ------------------------------------------------------------------
    if skeleton_dir is not None:
        _run_skeleton_mode(json_mode, cmd_name, skeleton_dir, token_budget)
        return

    # ------------------------------------------------------------------
    # Normal understand output
    # ------------------------------------------------------------------
    # W607-BC: per-phase substrate-CALL marker plumbing for the canonical
    # exploration aggregator. cmd_understand is the third member of the
    # exploration trio (cmd_describe W607-K + cmd_minimap W607-L + W607-AZ
    # + cmd_understand W607-BC) — agents invoke it as the single-call
    # orientation report covering structure, tech stack, architecture,
    # health, hotspots, and reading order. A raise in any one downstream
    # substrate (graph build, layer detection, cluster query, metrics
    # collection, conventions/complexity/patterns/debt detector, render,
    # serialize) previously bubbled as a Click traceback and dropped the
    # whole envelope. W607-BC surfaces each raise as a structured
    # ``understand_<phase>_failed:<exc_class>:<detail>`` marker and falls
    # back to a safe default so the remaining substrates still emit.
    # Closed-enum marker prefix ``understand_*`` (mirrors W607-K's
    # ``describe_*`` + W607-AZ/L's ``minimap_*`` family discipline).
    #
    # Empty bucket on the success path produces a byte-identical envelope
    # (no ``warnings_out`` key in either ``summary`` or top-level).
    _w607bc_warnings_out: list[str] = []

    def _run_check_bc(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-BC marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface a ``understand_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607bc_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607bc_warnings_out.append(f"understand_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    root = find_project_root()

    with open_db(readonly=True) as conn:
        # --- Basic stats ---
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        # --- Languages ---
        lang_rows = conn.execute(
            "SELECT language, COUNT(*) as cnt FROM files WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
        ).fetchall()
        languages = []
        for r in lang_rows:
            pct = round(r["cnt"] * 100 / file_count, 1) if file_count else 0
            languages.append(
                {
                    "name": r["language"],
                    "files": r["cnt"],
                    "pct": pct,
                }
            )

        # --- Tech stack ---
        frameworks = _run_check_bc("detect_frameworks", _detect_frameworks, conn, default=[])
        build_tool = _run_check_bc("detect_build", _detect_build, conn, default=None)

        # --- Architecture ---
        # W607-BC: wrap build_symbol_graph + detect_layers. Pre-existing
        # bare try/except now gets phase-scoped markers so a layer-detection
        # raise is distinguishable from a graph-build raise.
        def _build_graph_and_layers():
            from roam.graph.builder import build_symbol_graph
            from roam.graph.layers import detect_layers

            G_local = build_symbol_graph(conn)
            layer_map = detect_layers(G_local)
            return G_local, sorted(set(layer_map.values())) if layer_map else []

        _gl_result = _run_check_bc("build_graph_layers", _build_graph_and_layers, default=(None, []))
        G, layers = _gl_result if _gl_result is not None else (None, [])

        entry_points = _run_check_bc("find_entry_points", _find_entry_points, conn, default=[])
        key_abs = _run_check_bc(
            "key_abstractions",
            _key_abstractions,
            conn,
            limit=15 if full else 10,
            default=[],
        )

        # Clusters
        def _load_clusters():
            cluster_rows = conn.execute(
                "SELECT cluster_id, cluster_label, COUNT(*) as size FROM clusters GROUP BY cluster_id ORDER BY size DESC"
            ).fetchall()
            cd = []
            for cr in cluster_rows[: 20 if full else 8]:
                top_syms = conn.execute(
                    "SELECT s.name, s.kind FROM clusters c "
                    "JOIN symbols s ON c.symbol_id = s.id "
                    "WHERE c.cluster_id = ? "
                    "ORDER BY s.name LIMIT 5",
                    (cr["cluster_id"],),
                ).fetchall()
                cd.append(
                    {
                        "id": cr["cluster_id"],
                        "label": cr["cluster_label"] or f"cluster-{cr['cluster_id']}",
                        "size": cr["size"],
                        "top_symbols": [s["name"] for s in top_syms],
                    }
                )
            return cd

        clusters_data = _run_check_bc("load_clusters", _load_clusters, default=[])

        # --- Health ---
        def _collect_health():
            from roam.commands.metrics_history import collect_metrics

            return collect_metrics(conn)

        health = _run_check_bc(
            "collect_metrics",
            _collect_health,
            default={
                "health_score": 0,
                "cycles": 0,
                "god_components": 0,
                "bottlenecks": 0,
                "dead_exports": 0,
                "layer_violations": 0,
            },
        )

        # Worst issues
        worst = []
        if health["cycles"] > 0:
            worst.append(f"{health['cycles']} cycle(s)")
        if health["god_components"] > 0:
            worst.append(f"{health['god_components']} god component(s)")
        if health["dead_exports"] > 20:
            worst.append(f"{health['dead_exports']} dead exports")

        # --- Hotspots ---
        hotspots = _run_check_bc(
            "find_hotspots",
            _find_hotspots,
            conn,
            limit=20 if full else 10,
            default=[],
        )

        # --- Conventions ---
        conventions_summary = _run_check_bc("detect_conventions", _detect_conventions, conn, default={})

        # --- Complexity overview ---
        complexity_summary = _run_check_bc("complexity_overview", _complexity_overview, conn, default=None)

        # --- Patterns ---
        patterns_detected = _run_check_bc("detect_patterns", _detect_patterns_summary, conn, default=[])

        # --- Debt hotspots ---
        debt_hotspots = _run_check_bc("top_debt", _top_debt, conn, limit=5, default=[])

        # --- Reading order ---
        reading_order = _run_check_bc(
            "suggest_reading_order",
            _suggest_reading_order,
            conn,
            entry_points,
            key_abs,
            hotspots,
            default=[],
        )

        # ------------------------------------------------------------------
        # Flag: --tour  (gather tour data to append after normal output)
        # ------------------------------------------------------------------
        tour_data = None
        if tour_mode:
            tour_data = _run_check_bc("gather_tour_data", _gather_tour_data, conn, G, default=None)

        # --- JSON output ---
        if json_mode:
            _lang_names = "+".join(l["name"] for l in languages[:3])
            if len(languages) > 3:
                _lang_names += f"+{len(languages) - 3}more"
            # Pattern 3a / LAW 6 fix: route the score->label decision through
            # the canonical band map shared with ``roam health`` so the SAME
            # score never maps to two different verdict labels across
            # commands. ``health_band(75)`` == "Fair" exactly as
            # ``roam health`` prints it. (Was a divergent inline >=70
            # "healthy" cutoff — F2 in (internal memo).)
            _health_label = health_band(health["health_score"])
            _understand_verdict = (
                f"{_health_label} {len(languages)}-lang project "
                f"({health['health_score']}/100), "
                f"{len(clusters_data)} clusters, {len(hotspots)} hotspots"
            )
            # Unique-signal discovery hints (LAW 11: server-side hints).
            # Several roam commands produce signal not available elsewhere
            # (danger_score, AI-rot, AI-ratio, AI-readiness, cohesion,
            # algo anti-patterns, 30d forecast).  An agent reading
            # ``roam understand`` should know which command surfaces each
            # one without scraping prose.  See
            # `the dogfood synthesis notes` section "NEW in v3".
            discoverable_via = {
                "danger_score": "roam metrics-push --dry-run",
                "algo_anti_patterns": "roam algo",
                "ai_generated_percentage": "roam ai-ratio",
                "ai_readiness_score": "roam ai-readiness",
                "ai_rot_score": "roam vibe-check",
                "module_cohesion_pct": "roam module <module>",
                "health_30d_forecast": "roam forecast",
            }
            # ``next_steps`` is consumed by the formatter and surfaces as
            # ``agent_contract.next_commands`` — copy-paste-executable
            # roam invocations for advanced discovery.  Ordering: most
            # broadly-useful unique-signal commands first; standard
            # follow-ups after.
            next_steps = [
                "roam vibe-check",
                "roam ai-readiness",
                "roam ai-ratio",
                "roam algo",
                "roam forecast",
            ]
            envelope_kwargs = dict(
                project={
                    "name": root.name,
                    "root": str(root),
                    "files": file_count,
                    "symbols": sym_count,
                    "edges": edge_count,
                },
                tech_stack={
                    "languages": languages,
                    "frameworks": frameworks,
                    "build": build_tool,
                },
                architecture={
                    "layers": layers,
                    "layer_count": len(layers),
                    "entry_points": entry_points,
                    "key_abstractions": key_abs,
                    "clusters": clusters_data,
                },
                health_summary={
                    "score": health["health_score"],
                    "cycles": health["cycles"],
                    "god_components": health["god_components"],
                    "bottlenecks": health["bottlenecks"],
                    "dead_exports": health["dead_exports"],
                    "layer_violations": health["layer_violations"],
                    "worst_issues": worst,
                },
                conventions=conventions_summary,
                complexity=complexity_summary,
                patterns=patterns_detected,
                debt_hotspots=debt_hotspots,
                hotspots=hotspots,
                suggested_reading_order=reading_order,
                discoverable_via=discoverable_via,
                next_steps=next_steps,
            )
            if tour_data is not None:
                envelope_kwargs["tour"] = tour_data
            # W607-BC: thread the per-phase marker bucket into BOTH
            # summary.warnings_out and top-level warnings_out. Empty
            # bucket -> byte-identical envelope (the keys are only
            # added when non-empty). partial_success flips only when
            # any substrate raised.
            understand_summary: dict[str, object] = {
                "verdict": _understand_verdict,
                "files": file_count,
                "symbols": sym_count,
                "health_score": health["health_score"],
                "health_band": _health_label,
                # Pattern 3a sidecar: name the precise score->label source so
                # agents can see understand + health share the band map.
                "health_band_definition": health_band_definition(),
                "languages": len(languages),
                "caller_metric_definition": "direct_in_degree (architecture.key_abstractions[*].fan_in)",
            }
            combined_warnings = list(_w607bc_warnings_out)
            if combined_warnings:
                understand_summary["warnings_out"] = list(combined_warnings)
                understand_summary["partial_success"] = True
                envelope_kwargs["warnings_out"] = list(combined_warnings)
            # W607-BC: wrap JSON serialization itself — the last
            # downstream substrate. On serialize raise, fall back to a
            # minimal envelope so the contract still holds.
            envelope_text = _run_check_bc(
                "serialize_envelope",
                lambda: to_json(
                    json_envelope(
                        cmd_name,
                        summary=understand_summary,
                        budget=token_budget,
                        **envelope_kwargs,
                    )
                ),
                default=None,
            )
            if envelope_text is None:
                # serialize_envelope raised — re-merge bucket (it grew)
                # and produce a minimal envelope so the contract holds.
                combined_warnings = list(_w607bc_warnings_out)
                understand_summary["warnings_out"] = list(combined_warnings)
                understand_summary["partial_success"] = True
                envelope_text = to_json(
                    json_envelope(
                        cmd_name,
                        summary=understand_summary,
                        budget=token_budget,
                        warnings_out=list(combined_warnings),
                    )
                )
            click.echo(envelope_text)
            return

        # W607-BC: wrap the text-render substrate boundary. A raise here
        # (formatter import error, missing template, broken renderer)
        # would previously bubble as a Click traceback; surface as a
        # ``understand_render_text_failed:...`` marker. Text mode emits
        # the marker as a stderr-style note so the operator sees the
        # disclosure; the JSON envelope channel is reserved for json_mode.
        _run_check_bc(
            "render_text",
            _understand_text,
            root,
            file_count,
            sym_count,
            edge_count,
            languages,
            frameworks,
            build_tool,
            layers,
            clusters_data,
            health,
            worst,
            key_abs,
            entry_points,
            hotspots,
            conventions_summary,
            complexity_summary,
            patterns_detected,
            debt_hotspots,
            reading_order,
            default=None,
        )

        # Append tour output if --tour was given
        if tour_data is not None:
            _run_check_bc(
                "emit_tour_text",
                _emit_tour_text,
                tour_data,
                conn,
                G,
                mermaid_mode,
                default=None,
            )

        # W607-BC text-mode disclosure: surface non-empty marker bucket on
        # stderr so operators see substrate degradations even when running
        # without ``--json``. The text-mode happy path stays byte-identical.
        if _w607bc_warnings_out:
            for marker in _w607bc_warnings_out:
                click.echo(f"# warning: {marker}", err=True)


def _understand_text(
    root,
    file_count,
    sym_count,
    edge_count,
    languages,
    frameworks,
    build_tool,
    layers,
    clusters_data,
    health,
    worst,
    key_abs,
    entry_points,
    hotspots,
    conventions_summary,
    complexity_summary,
    patterns_detected,
    debt_hotspots,
    reading_order,
):
    """Emit compact text output for the understand command."""
    lang_str = ", ".join(f"{l['name']} ({l['files']})" for l in languages[:5])
    if len(languages) > 5:
        lang_str += f" +{len(languages) - 5} more"

    fw_str = ", ".join(frameworks) if frameworks else "none detected"
    build_str = build_tool or "unknown"

    _health_label = (
        "healthy" if health["health_score"] >= 70 else "moderate" if health["health_score"] >= 40 else "unhealthy"
    )
    _understand_verdict = (
        f"{_health_label} {len(languages)}-lang project "
        f"({health['health_score']}/100), "
        f"{len(clusters_data)} clusters, {len(hotspots)} hotspots"
    )
    click.echo(f"VERDICT: {_understand_verdict}")
    click.echo()
    click.echo(f"=== {root.name} ===\n")
    click.echo(f"Project: {file_count} files, {sym_count} symbols, {edge_count} edges")
    click.echo(f"Languages: {lang_str}")
    click.echo(f"Stack: {fw_str} | Build: {build_str}")
    click.echo(f"Architecture: {len(layers)} layers, {len(clusters_data)} clusters")
    click.echo(f"Health: {health['health_score']}/100 — {', '.join(worst) if worst else 'no critical issues'}")
    click.echo()

    click.echo(f"Key abstractions ({len(key_abs)}):")
    for ka in key_abs[:10]:
        click.echo(f"  {abbrev_kind(ka['kind'])}  {ka['name']:<40s}  fan_in={ka['fan_in']:<3d}  {ka['location']}")
    if len(key_abs) > 10:
        click.echo(f"  (+{len(key_abs) - 10} more)")
    click.echo()

    if entry_points:
        click.echo(f"Entry points ({len(entry_points)}):")
        for ep in entry_points[:5]:
            click.echo(f"  {ep['path']:<50s}  ({ep['symbols']} syms)")
        click.echo()

    if clusters_data:
        click.echo(f"Clusters ({len(clusters_data)}):")
        for cl in clusters_data[:8]:
            syms = ", ".join(cl["top_symbols"][:4])
            more = f" +{cl['size'] - 4}" if cl["size"] > 4 else ""
            click.echo(f"  {cl['label']:<30s}  {cl['size']:>3d} syms  [{syms}{more}]")
        if len(clusters_data) > 8:
            click.echo(f"  (+{len(clusters_data) - 8} more)")
        click.echo()

    if hotspots:
        click.echo(f"Hotspots ({len(hotspots)}):")
        for hs in hotspots[:5]:
            click.echo(
                f"  {hs['path']:<50s}  churn={hs['churn']:<5d}  "
                f"authors={hs['authors']}  coupling={hs['coupling_partners']}"
            )
        click.echo()

    if conventions_summary:
        parts = [f"{kind}: {info['style']} ({info['pct']:.0f}%)" for kind, info in conventions_summary.items()]
        click.echo(f"Conventions: {', '.join(parts)}")
        click.echo()

    if complexity_summary:
        click.echo(
            f"Complexity: {complexity_summary['total_analyzed']} functions, "
            f"avg={complexity_summary['avg']}, "
            f"{complexity_summary['critical']} critical, "
            f"{complexity_summary['high']} high"
        )
        if complexity_summary["worst"]:
            worst_names = ", ".join(f"{w['name']}({w['cc']})" for w in complexity_summary["worst"][:3])
            click.echo(f"  Worst: {worst_names}")
        click.echo()

    if patterns_detected:
        pat_str = ", ".join(f"{p['type']}: {p['name']} ({p['count']})" for p in patterns_detected)
        click.echo(f"Patterns: {pat_str}")
        click.echo()

    if debt_hotspots:
        click.echo("Debt hotspots:")
        for d in debt_hotspots:
            click.echo(f"  {d['path']:<50s}  complexity={d['complexity']:<6}  churn={d['churn']}")
        click.echo()

    click.echo("Suggested reading order:")
    for ro in reading_order:
        click.echo(f"  {ro['priority']:>2d}. {ro['path']:<50s}  ({ro['reason']})")

    # Advanced discovery — surface commands that produce signal not
    # available elsewhere (LAW 11: server-side hints teaching tools).
    # Compact: one line each, copy-paste-executable, ordered by breadth
    # of utility.  See `the dogfood synthesis notes`.
    click.echo()
    click.echo("Advanced discovery (unique signals):")
    click.echo("  roam metrics-push --dry-run   -- danger_score per file (churn × complexity × fan_in)")
    click.echo("  roam algo                     -- algorithmic anti-patterns (io-in-loop, list-prepend, …)")
    click.echo("  roam vibe-check               -- AI-rot score + pattern breakdown")
    click.echo("  roam ai-ratio                 -- ai_generated_percentage (commit signature)")
    click.echo("  roam ai-readiness             -- ai_readiness_score (7-dim scorecard)")
    click.echo("  roam module <dir>             -- cohesion_pct + API surface")
    click.echo("  roam forecast                 -- 30d health projection (Theil-Sen)")


# ---------------------------------------------------------------------------
# --tour helpers
# ---------------------------------------------------------------------------


def _gather_tour_data(conn, G):
    """Gather tour data (reading order, entry points, top symbols) for --tour flag.

    ``G`` may be None if the graph could not be built; in that case reading
    order and top symbols will fall back to empty lists.
    """
    from roam.commands.cmd_tour import _entry_points as _tour_entry_points
    from roam.commands.cmd_tour import _reading_order as _tour_reading_order
    from roam.commands.cmd_tour import _top_symbols as _tour_top_symbols

    if G is None:
        try:
            from roam.graph.builder import build_symbol_graph

            G = build_symbol_graph(conn)
        except sqlite3.Error:
            G = None

    reading_order = _tour_reading_order(conn, G) if G is not None else []
    entry_points = _tour_entry_points(conn)
    top_symbols = _tour_top_symbols(conn, G, limit=10) if G is not None else []

    return {
        "reading_order": reading_order,
        "entry_points": entry_points,
        "top_symbols": top_symbols,
    }


def _emit_tour_text(tour_data, conn, G, mermaid_mode):
    """Emit the tour section appended after normal understand text output."""
    click.echo()
    click.echo("=== Tour ===")
    click.echo()

    reading_order = tour_data.get("reading_order", [])
    entry_points = tour_data.get("entry_points", [])
    top_symbols = tour_data.get("top_symbols", [])

    if reading_order:
        click.echo("Reading order (layer-based, foundation first):")
        current_layer = -1
        for item in reading_order:
            if item["layer"] != current_layer:
                current_layer = item["layer"]
                lbl = "foundation" if current_layer == 0 else f"builds on layer {current_layer - 1}"
                click.echo(f"  Layer {current_layer} ({lbl}):")
            click.echo(f"    {item['file']}")
        click.echo()

    if entry_points:
        click.echo(f"Entry points ({len(entry_points)}):")
        for e in entry_points:
            click.echo(f"  {e['kind']:<6s} {e['name']:<40s} {e['location']}")
        click.echo()

    if top_symbols:
        click.echo("Top symbols (PageRank):")
        for s in top_symbols:
            click.echo(f"  {s['kind']:<6s} {s['name']:<40s} fan_in={s['fan_in']:<3d} {s['location']}")
        click.echo()

    click.echo("Next steps:")
    click.echo("  roam search <pattern>    -- find any symbol by name")
    click.echo("  roam context <symbol>    -- get files and line ranges to read")
    click.echo("  roam why <symbol>        -- understand why a symbol matters")
    click.echo("  roam preflight <symbol>  -- safety check before modifying")
    click.echo()

    if mermaid_mode:
        try:
            from roam.commands.cmd_tour import _tour_mermaid

            mermaid_text = _tour_mermaid(conn, G, top_symbols, reading_order)
            click.echo("Mermaid diagram:")
            click.echo(mermaid_text)
        except Exception as _exc:  # noqa: BLE001 — defensive
            from roam.observability import log_swallowed

            log_swallowed("cmd_understand:mermaid", _exc)


# ---------------------------------------------------------------------------
# --agent helpers
# ---------------------------------------------------------------------------


def _run_agent_mode(json_mode, cmd_name, token_budget=0):
    """Handle the --agent flag: emit a compact agent-oriented prompt."""
    from roam.commands.cmd_describe import _agent_prompt_data, _format_agent_prompt

    with open_db(readonly=True) as conn:
        data = _agent_prompt_data(conn)

    _ap_verdict = (
        f"{data.get('project', 'project')}: {data.get('files', 0)} files, "
        f"{data.get('languages', 'unknown')} | health={data.get('health_score', 'N/A')}"
    )
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    cmd_name,
                    summary={"verdict": _ap_verdict, "mode": "agent"},
                    budget=token_budget,
                    agent_prompt=_format_agent_prompt(data),
                    **data,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {_ap_verdict}")
        click.echo()
        click.echo(_format_agent_prompt(data))


# ---------------------------------------------------------------------------
# --skeleton helpers
# ---------------------------------------------------------------------------


def _run_skeleton_mode(json_mode, cmd_name, directory, token_budget=0):
    """Handle the --skeleton DIR flag: emit directory skeleton."""
    from collections import defaultdict

    from roam.output.formatter import format_signature

    directory = directory.replace("\\", "/").rstrip("/")

    with open_db(readonly=True) as conn:
        # W1311: track which resolution tier the skeleton input hit. The
        # legacy code silently fell back from an exact ``directory/%`` prefix
        # match to a ``%directory/%`` substring match and emitted the same
        # success verdict for both tiers — a textbook Pattern-1 Variant D
        # silent-success-on-degraded-resolution shape. Disclose the tier via
        # ``resolution_disclosure`` so agents can branch on ``file`` vs
        # ``file_substring`` vs ``unresolved``.
        symbols = conn.execute(
            "SELECT s.*, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE REPLACE(f.path, '\\', '/') LIKE ? AND s.is_exported = 1 "
            "ORDER BY f.path, s.line_start",
            (f"{directory}/%",),
        ).fetchall()
        skeleton_tier = "file" if symbols else None

        if not symbols:
            # Try partial match — degraded resolution tier (W1311).
            symbols = conn.execute(
                "SELECT s.*, f.path as file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                "WHERE REPLACE(f.path, '\\', '/') LIKE ? AND s.is_exported = 1 "
                "ORDER BY f.path, s.line_start",
                (f"%{directory}/%",),
            ).fetchall()
            if symbols:
                skeleton_tier = "file_substring"

        if not symbols:
            disclosure = resolution_disclosure("unresolved", target=directory)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            cmd_name,
                            summary={
                                "verdict": f"no symbols found in {directory}/",
                                "file_count": 0,
                                "symbol_count": 0,
                                "resolution": disclosure["resolution"],
                                "partial_success": disclosure["partial_success"],
                            },
                            budget=token_budget,
                            directory=directory,
                            files={},
                            symbol_count=0,
                            resolution=disclosure,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: no symbols found in {directory}/\n")
                click.echo(f"No exported symbols found in: {directory}/")
                click.echo("Hint: use a path relative to the project root.")
            return

        # Group by file
        by_file = defaultdict(list)
        for s in symbols:
            by_file[s["file_path"]].append(s)

        # W1311 disclosure: stamp the resolved tier ("file" exact-prefix vs
        # "file_substring" %directory% fallback) onto the success envelope so
        # the verdict reflects degraded resolution. The text-mode verdict
        # carries a "[file substring match]" suffix mirroring preflight's
        # convention.
        skeleton_disclosure = resolution_disclosure(
            skeleton_tier or "file",
            target=directory,
        )
        verdict_suffix = " [file substring match]" if skeleton_tier == "file_substring" else ""

        if json_mode:
            _verdict = f"{directory}/: {len(by_file)} files, {len(symbols)} symbols{verdict_suffix}"
            result = {}
            for fp in sorted(by_file.keys()):
                result[fp] = [
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "signature": s["signature"] or "",
                        "line_start": s["line_start"],
                        "line_end": s["line_end"],
                        "docstring": (s["docstring"] or "").strip().split("\n")[0][:80] if s["docstring"] else "",
                    }
                    for s in by_file[fp]
                ]
            click.echo(
                to_json(
                    json_envelope(
                        cmd_name,
                        summary={
                            "verdict": _verdict,
                            "file_count": len(by_file),
                            "symbol_count": len(symbols),
                            "resolution": skeleton_disclosure["resolution"],
                            "partial_success": skeleton_disclosure["partial_success"],
                        },
                        budget=token_budget,
                        directory=directory,
                        file_count=len(by_file),
                        symbol_count=len(symbols),
                        files=result,
                        resolution=skeleton_disclosure,
                    )
                )
            )
            return

        file_count = len(by_file)
        sym_count = len(symbols)
        _verdict = f"{directory}/: {file_count} files, {sym_count} exported symbols{verdict_suffix}"
        click.echo(f"VERDICT: {_verdict}\n")
        click.echo(f"{directory}/ ({file_count} files, {sym_count} exported symbols)")
        if skeleton_tier == "file_substring":
            click.echo("  Note: substring match on directory path — input was not an exact directory prefix.")
        click.echo()

        # Build parent lookup for indentation
        parent_ids = {s["id"]: s["parent_id"] for s in symbols}
        parent_set = {s["id"] for s in symbols}

        for file_path in sorted(by_file.keys()):
            file_syms = by_file[file_path]
            click.echo(f"  {file_path}")

            for s in file_syms:
                # Compute indentation level
                level = 0
                if s["parent_id"] is not None and s["parent_id"] in parent_set:
                    level = 1
                    pid = s["parent_id"]
                    while pid in parent_ids and parent_ids[pid] is not None and parent_ids[pid] in parent_set:
                        level += 1
                        pid = parent_ids[pid]

                prefix = "    " + "  " * level
                kind = abbrev_kind(s["kind"])
                sig = format_signature(s["signature"], max_len=40)
                line_info = f"L{s['line_start']}"
                if s["line_end"] and s["line_end"] != s["line_start"]:
                    line_info += f"-{s['line_end']}"

                doc_snippet = ""
                if s["docstring"]:
                    first_line = s["docstring"].strip().split("\n")[0].strip()
                    if len(first_line) > 50:
                        first_line = first_line[:47] + "..."
                    doc_snippet = f"  {first_line}"

                parts = [f"{kind:<6s}", s["name"]]
                if sig:
                    parts.append(sig)
                parts.append(line_info)

                click.echo(f"{prefix}{'  '.join(parts)}{doc_snippet}")

            click.echo()
