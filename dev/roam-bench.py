#!/usr/bin/env python3
"""Roam benchmark - automated quality and performance metrics across public repos."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from functools import partial
from pathlib import Path

# Force unbuffered output so progress is visible in real-time
print = partial(print, flush=True)

# ── Repo catalogue ──────────────────────────────────────────────────────────

REPOS = {
    "fastapi":      {"url": "https://github.com/tiangolo/fastapi.git",       "lang": "py"},
    "django":       {"url": "https://github.com/django/django.git",          "lang": "py"},
    "cli":          {"url": "https://github.com/cli/cli.git",                "lang": "go"},
    "gin":          {"url": "https://github.com/gin-gonic/gin.git",          "lang": "go"},
    "express":      {"url": "https://github.com/expressjs/express.git",      "lang": "js"},
    "axios":        {"url": "https://github.com/axios/axios.git",            "lang": "js"},
    "svelte":       {"url": "https://github.com/sveltejs/svelte.git",        "lang": "ts"},
    "vue":          {"url": "https://github.com/vuejs/core.git",             "lang": "ts"},
    "nextjs":       {"url": "https://github.com/vercel/next.js.git",         "lang": "ts"},
    "ripgrep":      {"url": "https://github.com/BurntSushi/ripgrep.git",     "lang": "rs"},
    "tokio":        {"url": "https://github.com/tokio-rs/tokio.git",         "lang": "rs"},
    "spring-boot":  {"url": "https://github.com/spring-projects/spring-boot.git", "lang": "java"},
    "laravel":      {"url": "https://github.com/laravel/framework.git",          "lang": "php"},
}

SLOW_REPOS = {"django", "spring-boot", "nextjs"}

ALL_COMMANDS = [
    "index", "map", "file", "symbol", "trace", "deps", "module", "health",
    "clusters", "layers", "weather", "dead", "search", "grep", "uses",
    "impact", "owner", "coupling", "fan", "diff", "describe", "test-map",
    "sketch", "context", "safe-delete", "pr-risk", "split", "risk", "why",
]

BENCH_DIR = Path(__file__).resolve().parent / "bench-repos"

# ── Helpers ─────────────────────────────────────────────────────────────────

def run(cmd, cwd=None, timeout=120, capture=True):
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, capture_output=capture,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def roam_cmd(args, cwd, timeout=120):
    """Run a roam CLI command via sys.executable -m roam."""
    return run([sys.executable, "-m", "roam"] + args, cwd=cwd, timeout=timeout)


def head_commit(repo_dir):
    """Get HEAD commit hash."""
    rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir)
    return out.strip() if rc == 0 else "unknown"


def roam_commit():
    """Get the roam-code repo's current commit."""
    roam_root = Path(__file__).resolve().parent
    rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=roam_root)
    return out.strip() if rc == 0 else "unknown"


def fmt_duration(secs):
    """Format seconds as human readable."""
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s"


def bar(value, max_val=10, width=20):
    """Simple ASCII bar chart."""
    filled = int(value / max_val * width)
    return "#" * filled + "." * (width - filled)


# ── Phase 1: Clone / update repos ──────────────────────────────────────────

def setup_repos(names):
    BENCH_DIR.mkdir(exist_ok=True)
    for i, name in enumerate(names, 1):
        repo_dir = BENCH_DIR / name
        url = REPOS[name]["url"]
        if repo_dir.exists():
            print(f"  [{i}/{len(names)}] {name}: updating ...")
            run(["git", "fetch", "--depth", "1"], cwd=repo_dir, timeout=120)
            run(["git", "reset", "--hard", "origin/HEAD"], cwd=repo_dir, timeout=60)
        else:
            print(f"  [{i}/{len(names)}] {name}: cloning ...")
            rc, _, err = run(
                ["git", "clone", "--depth", "1", url, str(repo_dir)],
                timeout=300,
            )
            if rc != 0:
                print(f"    CLONE FAILED: {err[:200]}")


# ── Phase 2: Index repos ───────────────────────────────────────────────────

def index_repo(name, idx, total):
    repo_dir = BENCH_DIR / name
    print(f"  [{idx}/{total}] {name}: indexing ...")
    t0 = time.time()
    rc, out, err = roam_cmd(["index", "--force"], cwd=repo_dir, timeout=600)
    elapsed = time.time() - t0
    if rc != 0:
        print(f"    INDEX FAILED ({fmt_duration(elapsed)}): {err[:300]}")
        return None
    # Show quick stats from DB
    conn = open_db(repo_dir)
    if conn:
        f, s, e = basic_counts(conn)
        rate = f / elapsed if elapsed > 0 else 0
        print(f"    done in {fmt_duration(elapsed)} - {f} files, {s} symbols, "
              f"{e} edges ({rate:.0f} files/s)")
        conn.close()
    else:
        print(f"    done in {fmt_duration(elapsed)}")
    return elapsed


# ── Phase 3: Measure quality metrics ───────────────────────────────────────

def open_db(repo_dir):
    db_path = repo_dir / ".roam" / "index.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def basic_counts(conn):
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    return files, symbols, edges


def language_breakdown(conn):
    """Count files per detected language."""
    rows = conn.execute(
        "SELECT language, COUNT(*) as cnt FROM files "
        "WHERE language IS NOT NULL GROUP BY language ORDER BY cnt DESC"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def symbol_kind_breakdown(conn):
    """Count symbols per kind."""
    rows = conn.execute(
        "SELECT kind, COUNT(*) as cnt FROM symbols GROUP BY kind ORDER BY cnt DESC"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def edge_kind_breakdown(conn):
    """Count edges per kind."""
    rows = conn.execute(
        "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


CODE_LANGUAGES = {
    "python", "javascript", "typescript", "go", "rust", "java", "c", "cpp",
    "csharp", "ruby", "php", "swift", "kotlin", "scala", "vue", "svelte",
    "tsx", "jsx",
}


def symbol_coverage(conn):
    """% of code files that have at least one symbol.
    Excludes docs/config/data files, build artifacts, and empty files."""
    lang_filter = ",".join(f"'{l}'" for l in CODE_LANGUAGES)
    # Exclude build artifacts, vendored code, minified files, and empty __init__.py
    # Exclude build artifacts, vendored code, minified files, test/fixture dirs
    # Each pattern needs both slash variants (fwd/back) AND root-level start
    skip_dirs = [
        "dist", "vendor", "build", "node_modules",
        "__tests__", "tests", "test", "__test__",
        "spec", "__benchmarks__", "fixtures", "__fixtures__",
    ]
    parts = ["AND path NOT LIKE '%.min.js' AND path NOT LIKE '%.min.css'"]
    for d in skip_dirs:
        parts.append(f"AND path NOT LIKE '%/{d}/%' AND path NOT LIKE '%\\{d}\\%'")
        parts.append(f"AND path NOT LIKE '{d}/%' AND path NOT LIKE '{d}\\%'")
    exclusion = "\n        ".join(parts)
    code_files = conn.execute(
        f"SELECT COUNT(*) FROM files WHERE language IN ({lang_filter}) {exclusion}"
    ).fetchone()[0]
    if code_files == 0:
        code_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if code_files == 0:
            return 0.0, 0
    files_with_symbols = conn.execute(
        f"SELECT COUNT(DISTINCT s.file_id) FROM symbols s "
        f"JOIN files f ON f.id = s.file_id "
        f"WHERE f.language IN ({lang_filter}) {exclusion}"
    ).fetchone()[0]
    return (files_with_symbols / code_files) * 100, code_files


def same_file_misresolutions(conn):
    """Edges where src and tgt are in the same file, tgt name is ambiguous,
    AND the qualified_name didn't disambiguate (multiple symbols share the
    same qualified_name in that file, or qualified_name == name)."""
    conn.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _ambig_same_file AS
        SELECT file_id, name
        FROM symbols
        GROUP BY file_id, name
        HAVING COUNT(*) > 1
    """)
    # Only count as misresolution if the qualified_name is also ambiguous
    # (i.e., qualified_name == name, meaning no parent context helped)
    # Edges where qualified_name contains :: or . are disambiguated and correct
    row = conn.execute("""
        SELECT COUNT(*) FROM edges e
        JOIN symbols s ON e.source_id = s.id
        JOIN symbols t ON e.target_id = t.id
        WHERE s.file_id = t.file_id
          AND EXISTS (
              SELECT 1 FROM _ambig_same_file a
              WHERE a.file_id = t.file_id AND a.name = t.name
          )
          AND t.qualified_name = t.name
    """).fetchone()
    return row[0]


def cross_file_ambiguity(conn):
    """Edges where the target symbol's name appears in >1 distinct file,
    AND the qualified_name doesn't disambiguate (qualified_name == name or
    qualified_name also appears in >1 file)."""
    conn.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _ambig_cross_file AS
        SELECT name
        FROM symbols
        GROUP BY name
        HAVING COUNT(DISTINCT file_id) > 1
    """)
    conn.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _ambig_qualified AS
        SELECT qualified_name
        FROM symbols
        GROUP BY qualified_name
        HAVING COUNT(DISTINCT file_id) > 1
    """)
    # Edge is truly ambiguous only if:
    # 1. Target name appears in >1 file
    # 2. AND target's qualified_name doesn't help (equals name, or also in >1 file)
    row = conn.execute("""
        SELECT COUNT(*) FROM edges e
        JOIN symbols t ON e.target_id = t.id
        WHERE EXISTS (
            SELECT 1 FROM _ambig_cross_file a WHERE a.name = t.name
        )
        AND (
            t.qualified_name = t.name
            OR EXISTS (
                SELECT 1 FROM _ambig_qualified q WHERE q.qualified_name = t.qualified_name
            )
        )
    """).fetchone()
    return row[0]


def dead_code_high_conf(conn):
    """Exported symbols with no incoming edges, in files that ARE imported by others."""
    row = conn.execute("""
        SELECT COUNT(*) FROM symbols s
        WHERE s.is_exported = 1
          AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target_id = s.id)
          AND EXISTS (
              SELECT 1 FROM file_edges fe WHERE fe.target_file_id = s.file_id
          )
    """).fetchone()
    return row[0]


def hidden_coupling_pct(conn):
    """Top-50 co-change pairs where neither direction has a file_edge with symbol_count >= 2."""
    rows = conn.execute("""
        SELECT file_id_a, file_id_b FROM git_cochange
        ORDER BY cochange_count DESC LIMIT 50
    """).fetchall()
    if not rows:
        return 0.0
    hidden = 0
    for r in rows:
        a, b = r[0], r[1]
        has_edge = conn.execute("""
            SELECT 1 FROM file_edges
            WHERE ((source_file_id = ? AND target_file_id = ?)
                OR (source_file_id = ? AND target_file_id = ?))
              AND symbol_count >= 2
            LIMIT 1
        """, (a, b, b, a)).fetchone()
        if not has_edge:
            hidden += 1
    return (hidden / len(rows)) * 100


def graph_richness(conn, repo_dir):
    """Compute layer count, cycle count, cluster count using roam internals."""
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        layers = detect_layers(G)
        layer_count = (max(layers.values()) + 1) if layers else 0
        cycles = find_cycles(G)
        cycle_count = len(cycles)
    except Exception as e:
        print(f"    graph richness error: {e}")
        layer_count = 0
        cycle_count = 0

    cluster_count = conn.execute(
        "SELECT COUNT(DISTINCT cluster_id) FROM clusters"
    ).fetchone()[0]

    return layer_count, cycle_count, cluster_count


def file_edge_count(conn):
    """Count file-level edges."""
    return conn.execute("SELECT COUNT(*) FROM file_edges").fetchone()[0]


def cross_file_edge_ratio(conn):
    """% of edges that cross file boundaries (source_file != target_file)."""
    total = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    if total == 0:
        return 0.0
    cross = conn.execute("""
        SELECT COUNT(*) FROM edges e
        JOIN symbols s ON e.source_id = s.id
        JOIN symbols t ON e.target_id = t.id
        WHERE s.file_id != t.file_id
    """).fetchone()[0]
    return round((cross / total) * 100, 1)


def symbol_reachability(conn):
    """% of symbols that have at least one edge (incoming or outgoing)."""
    total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    if total == 0:
        return 0.0
    connected = conn.execute("""
        SELECT COUNT(DISTINCT id) FROM (
            SELECT source_id as id FROM edges
            UNION
            SELECT target_id as id FROM edges
        )
    """).fetchone()[0]
    return round((connected / total) * 100, 1)


def qualified_name_usage(conn):
    """% of symbols where qualified_name differs from name (scope resolution)."""
    total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    if total == 0:
        return 0.0
    qualified = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE qualified_name != name AND qualified_name IS NOT NULL"
    ).fetchone()[0]
    return round((qualified / total) * 100, 1)


def orphan_file_rate(conn):
    """% of code files not involved in any file_edge (neither source nor target)."""
    lang_filter = ",".join(f"'{l}'" for l in CODE_LANGUAGES)
    total = conn.execute(
        f"SELECT COUNT(*) FROM files WHERE language IN ({lang_filter})"
    ).fetchone()[0]
    if total == 0:
        return 0.0
    connected = conn.execute(f"""
        SELECT COUNT(DISTINCT id) FROM (
            SELECT source_file_id as id FROM file_edges
            UNION
            SELECT target_file_id as id FROM file_edges
        )
    """).fetchone()[0]
    return round(((total - connected) / total) * 100, 1)


def measure(name, index_time):
    repo_dir = BENCH_DIR / name
    conn = open_db(repo_dir)
    if conn is None:
        return None

    files, symbols, edges = basic_counts(conn)
    cov, code_files = symbol_coverage(conn)
    misres = same_file_misresolutions(conn)
    ambig = cross_file_ambiguity(conn)
    dead = dead_code_high_conf(conn)
    hidden = hidden_coupling_pct(conn)
    edge_density = edges / symbols if symbols > 0 else 0
    misres_rate = (misres / edges * 100) if edges > 0 else 0
    ambig_rate = (ambig / edges * 100) if edges > 0 else 0
    f_edges = file_edge_count(conn)

    langs = language_breakdown(conn)
    sym_kinds = symbol_kind_breakdown(conn)
    edge_kinds = edge_kind_breakdown(conn)

    layers, cycles, clusters = graph_richness(conn, repo_dir)

    # New enriched metrics
    xfile_ratio = cross_file_edge_ratio(conn)
    reachability = symbol_reachability(conn)
    qname_usage = qualified_name_usage(conn)
    orphan_rate = orphan_file_rate(conn)

    conn.close()

    return {
        "index": {
            "time_s": round(index_time, 2) if index_time else None,
            "files": files,
            "code_files": code_files,
            "symbols": symbols,
            "edges": edges,
            "file_edges": f_edges,
        },
        "quality": {
            "symbol_coverage_pct": round(cov, 1),
            "same_file_misres": misres,
            "misres_rate_pct": round(misres_rate, 2),
            "cross_file_ambig": ambig,
            "ambig_rate_pct": round(ambig_rate, 1),
            "edge_density": round(edge_density, 2),
            "dead_high_conf": dead,
            "hidden_coupling_pct": round(hidden, 1),
            "layers": layers,
            "cycles": cycles,
            "clusters": clusters,
            "cross_file_edge_pct": xfile_ratio,
            "symbol_reachability_pct": reachability,
            "qualified_name_pct": qname_usage,
            "orphan_file_pct": orphan_rate,
        },
        "breakdown": {
            "languages": langs,
            "symbol_kinds": sym_kinds,
            "edge_kinds": edge_kinds,
        },
    }


# ── Phase 4: Command validation ────────────────────────────────────────────

def sample_args(conn):
    """Pick sample args for commands that need them."""
    # Get a file path with symbols (more interesting than random)
    file_row = conn.execute(
        "SELECT f.path FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "GROUP BY f.id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    file_path = file_row[0] if file_row else ""

    # Get two different symbol names for trace (src != dst)
    sym_rows = conn.execute(
        "SELECT DISTINCT name FROM symbols "
        "WHERE kind IN ('function','method','class') "
        "ORDER BY RANDOM() LIMIT 2"
    ).fetchall()
    sym_name = sym_rows[0][0] if sym_rows else ""
    sym_name2 = sym_rows[1][0] if len(sym_rows) > 1 else sym_name

    # Get a directory with symbols
    if file_path:
        dir_path = str(Path(file_path).parent)
        if dir_path == ".":
            dir_path = ""
    else:
        dir_path = ""

    return file_path, sym_name, sym_name2, dir_path


def validate_commands(name):
    """Run all 28 commands, return results dict with per-command timing."""
    repo_dir = BENCH_DIR / name
    conn = open_db(repo_dir)
    if conn is None:
        return {"total": 28, "passed": 0, "failed": 28, "failures": ["NO DB"],
                "timings": {}, "sampled_args": {}}

    file_path, sym_name, sym_name2, dir_path = sample_args(conn)
    conn.close()

    sampled = {"file": file_path, "symbol": sym_name, "symbol2": sym_name2, "dir": dir_path}
    print(f"    args: file={file_path}")
    print(f"          sym={sym_name}, sym2={sym_name2}")
    print(f"          dir={dir_path}")

    # Build command invocations
    cmds = {
        # No-arg commands
        "map":      ["map"],
        "health":   ["health"],
        "clusters": ["clusters"],
        "layers":   ["layers"],
        "dead":     ["dead"],
        "coupling": ["coupling"],
        "weather":  ["weather"],
        "fan":      ["fan", "symbol"],
        "describe": ["describe"],
        "diff":     ["diff"],
        # With-arg commands
        "file":     ["file", file_path] if file_path else None,
        "deps":     ["deps", file_path] if file_path else None,
        "owner":    ["owner", file_path] if file_path else None,
        "test-map": ["test-map", sym_name] if sym_name else None,
        "symbol":   ["symbol", sym_name] if sym_name else None,
        "uses":     ["uses", sym_name] if sym_name else None,
        "impact":   ["impact", sym_name] if sym_name else None,
        "search":   ["search", sym_name] if sym_name else None,
        "trace":    ["trace", sym_name, sym_name2] if sym_name and sym_name2 else None,
        "module":   ["module", dir_path] if dir_path else None,
        "sketch":   ["sketch", dir_path] if dir_path else None,
        "grep":     ["grep", sym_name] if sym_name else None,
        # Compound / new commands
        "context":     ["context", sym_name] if sym_name else None,
        "safe-delete": ["safe-delete", sym_name] if sym_name else None,
        "pr-risk":     ["pr-risk"],
        "split":       ["split", file_path] if file_path else None,
        "risk":        ["risk"],
        "why":         ["why", sym_name] if sym_name else None,
    }

    passed = 0
    failures = []
    timings = {}

    for cmd_name in ALL_COMMANDS:
        if cmd_name == "index":
            passed += 1
            timings[cmd_name] = 0.0
            continue

        args = cmds.get(cmd_name)
        if args is None:
            passed += 1
            timings[cmd_name] = 0.0
            continue

        full_cmd = "roam " + " ".join(args)
        t0 = time.time()
        rc, stdout, err = roam_cmd(args, cwd=repo_dir, timeout=120)
        elapsed = round(time.time() - t0, 2)
        timings[cmd_name] = elapsed

        if rc == 0:
            passed += 1
            # Count output lines for context
            out_lines = len(stdout.strip().split("\n")) if stdout.strip() else 0
            # Warn if output looks suspect
            min_expected = {"map": 3, "health": 3, "file": 3, "describe": 10,
                           "layers": 3, "clusters": 3, "fan": 3, "sketch": 1}
            warn = ""
            if cmd_name in min_expected and out_lines < min_expected[cmd_name]:
                warn = f"  [WARN: only {out_lines} lines]"
            print(f"    ok    {elapsed:>6.1f}s  {full_cmd}  ({out_lines} lines){warn}")
        else:
            snippet = err.strip().split("\n")
            # Get last meaningful error line
            err_line = ""
            for line in reversed(snippet):
                line = line.strip()
                if line and not line.startswith("Traceback"):
                    err_line = line[:150]
                    break
            if not err_line:
                err_line = snippet[-1][:150] if snippet else f"exit {rc}"

            failures.append({
                "command": cmd_name,
                "invocation": full_cmd,
                "time_s": elapsed,
                "exit_code": rc,
                "error": err_line,
                "stderr": err.strip()[:500],
            })
            print(f"    FAIL  {elapsed:>6.1f}s  {full_cmd}")
            print(f"                     -> {err_line}")

    total = len(ALL_COMMANDS)
    total_time = sum(timings.values())
    print(f"    ---- {passed}/{total} passed in {fmt_duration(total_time)}")

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "failures": failures,
        "timings": timings,
        "sampled_args": sampled,
    }


# ── Composite score ────────────────────────────────────────────────────────

def clamp(v, lo=0.0, hi=10.0):
    return max(lo, min(hi, v))


def score_coverage(pct):
    """100%->10, 70%->7, 30%->3, 0%->0. Linear."""
    return clamp(pct / 10.0)


def score_misres(rate):
    """0%->10, 5%->0 (inverted)."""
    if rate <= 0:
        return 10.0
    if rate >= 5:
        return 0.0
    return 10.0 - (rate / 5) * 10


def score_ambiguity(rate):
    """0%->10, 50%->5, 100%->0."""
    return clamp(10.0 - (rate / 10.0))


def score_edge_density(ed):
    """<0.1->1, 0.5-3.0->8-10, >5->declining."""
    if ed < 0.1:
        return 1.0
    if ed < 0.5:
        return 1.0 + (ed - 0.1) / 0.4 * 7.0  # 1->8
    if ed <= 3.0:
        return 8.0 + (ed - 0.5) / 2.5 * 2.0  # 8->10
    if ed <= 5.0:
        return 10.0 - (ed - 3.0) / 2.0 * 3.0  # 10->7
    return clamp(7.0 - (ed - 5.0))


def score_graph_richness(layers, clusters, cycles):
    """Layers contribute most (depth of architecture), clusters and cycles add signal."""
    # layers: 1->1, 5->5, 10->7, 20+->9
    layer_s = clamp(layers * 0.7, 0, 9) if layers > 0 else 0
    # cycles: having some is natural, many = well-connected
    cycle_s = clamp(cycles * 0.3, 0, 3)
    # clusters: having many distinct clusters = good modularity signal
    cluster_s = clamp(clusters / 200, 0, 3)
    return clamp(layer_s + cycle_s + cluster_s)


def score_command_pass(passed, total):
    """100%->10, proportional."""
    if total == 0:
        return 10.0
    return (passed / total) * 10


def compute_sub_scores(quality, commands):
    """Return individual sub-scores dict and composite."""
    weights = {
        "coverage":   2.0,
        "misres":     2.5,
        "ambiguity":  1.0,
        "density":    1.5,
        "richness":   1.0,
        "commands":   1.5,
    }
    scores = {
        "coverage":  round(score_coverage(quality["symbol_coverage_pct"]), 1),
        "misres":    round(score_misres(quality["misres_rate_pct"]), 1),
        "ambiguity": round(score_ambiguity(quality["ambig_rate_pct"]), 1),
        "density":   round(score_edge_density(quality["edge_density"]), 1),
        "richness":  round(score_graph_richness(quality["layers"], quality["clusters"], quality["cycles"]), 1),
        "commands":  round(score_command_pass(commands["passed"], commands["total"]), 1),
    }
    total_weight = sum(weights.values())
    composite = round(sum(scores[k] * weights[k] for k in weights) / total_weight, 2)
    return scores, weights, composite


# ── Phase 5: Report ────────────────────────────────────────────────────────

def print_repo_card(name, data):
    """Print a detailed per-repo summary card."""
    idx = data["index"]
    q = data["quality"]
    bd = data["breakdown"]
    lang = REPOS[name]["lang"]
    scores, weights, composite = compute_sub_scores(q, data["commands"])

    # Header
    print(f"\n  {'=' * 60}")
    print(f"  {name.upper()} ({lang})  -  score {composite}/10")
    print(f"  {'=' * 60}")

    # Index stats
    t = fmt_duration(idx["time_s"]) if idx["time_s"] else "n/a"
    cf = idx.get('code_files', '?')
    print(f"  Index: {t} | {idx['files']} files ({cf} code) | {idx['symbols']} symbols | "
          f"{idx['edges']} edges | {idx['file_edges']} file-edges")

    # Language breakdown (top 5)
    if bd["languages"]:
        top_langs = list(bd["languages"].items())[:5]
        parts = [f"{l}={c}" for l, c in top_langs]
        print(f"  Languages: {', '.join(parts)}")

    # Symbol kinds (top 5)
    if bd["symbol_kinds"]:
        top_kinds = list(bd["symbol_kinds"].items())[:5]
        parts = [f"{k}={c}" for k, c in top_kinds]
        print(f"  Symbols: {', '.join(parts)}")

    # Edge kinds
    if bd["edge_kinds"]:
        top_ekinds = list(bd["edge_kinds"].items())[:5]
        parts = [f"{k}={c}" for k, c in top_ekinds]
        print(f"  Edges: {', '.join(parts)}")

    # Quality metrics
    print(f"  Coverage: {q['symbol_coverage_pct']:.1f}% | "
          f"MisRes: {q['same_file_misres']} ({q['misres_rate_pct']:.2f}%) | "
          f"Ambiguity: {q['cross_file_ambig']} ({q['ambig_rate_pct']:.1f}%)")
    print(f"  E/S: {q['edge_density']:.2f} | Dead: {q['dead_high_conf']} | "
          f"Hidden coupling: {q['hidden_coupling_pct']:.1f}%")
    print(f"  Layers: {q['layers']} | Cycles: {q['cycles']} | Clusters: {q['clusters']}")
    # Enriched quality metrics
    print(f"  Cross-file edges: {q.get('cross_file_edge_pct', 0)}% | "
          f"Reachability: {q.get('symbol_reachability_pct', 0)}% | "
          f"Qualified: {q.get('qualified_name_pct', 0)}% | "
          f"Orphan files: {q.get('orphan_file_pct', 0)}%")

    # Score breakdown with bars
    print(f"  Score breakdown:")
    for dim in ["coverage", "misres", "ambiguity", "density", "richness", "commands"]:
        s = scores[dim]
        w = weights[dim]
        print(f"    {dim:<11} {bar(s):>20}  {s:>4.1f}/10  (x{w})")
    print(f"    {'COMPOSITE':<11} {bar(composite):>20}  {composite:>4.1f}/10")

    # Command results
    cmds = data["commands"]
    if cmds["failures"]:
        print(f"  Commands: {cmds['passed']}/{cmds['total']} passed")
        for f in cmds["failures"]:
            print(f"    FAIL: {f['invocation']}  ({f['time_s']}s, exit {f.get('exit_code', '?')})")
            print(f"          {f['error']}")


def print_table(results):
    """Compact comparison table."""
    hdr = (f"{'Repo':<14} {'Lang':>4} {'Files':>6} {'Syms':>7} {'Edges':>7} "
           f"{'Cov%':>5} {'MisR%':>6} {'Ambig%':>6} {'E/S':>5} {'Lyrs':>4} "
           f"{'Cmds':>5} {'Score':>6}")
    print()
    print(hdr)
    print("-" * len(hdr))

    scores = []
    for name, data in sorted(results.items()):
        if data is None:
            print(f"{name:<14} {'---':>4}  FAILED")
            continue
        idx = data["index"]
        q = data["quality"]
        s = data["score"]
        lang = REPOS[name]["lang"]
        cp = f"{data['commands']['passed']}/{data['commands']['total']}"
        print(
            f"{name:<14} {lang:>4} {idx['files']:>6} {idx['symbols']:>7} "
            f"{idx['edges']:>7} {q['symbol_coverage_pct']:>5.1f} "
            f"{q['misres_rate_pct']:>6.2f} {q['ambig_rate_pct']:>6.1f} "
            f"{q['edge_density']:>5.2f} {q['layers']:>4} "
            f"{cp:>5} {s:>6.2f}"
        )
        scores.append(s)

    if scores:
        avg = sum(scores) / len(scores)
        print("-" * len(hdr))
        print(f"{'AVERAGE':<14} {'':>4} {'':>6} {'':>7} {'':>7} "
              f"{'':>5} {'':>6} {'':>6} {'':>5} {'':>4} "
              f"{'':>5} {avg:>6.2f}")
    print()


def print_enriched_table(results):
    """Enriched metrics table showing deeper quality indicators."""
    hdr = (f"{'Repo':<14} {'XFile%':>6} {'Reach%':>6} {'Qual%':>5} "
           f"{'Orphan%':>7} {'Dead':>5} {'HidCpl%':>7} {'Score':>6}")
    print()
    print("Enriched Quality Metrics:")
    print(hdr)
    print("-" * len(hdr))
    for name, data in sorted(results.items()):
        if data is None:
            continue
        q = data["quality"]
        s = data["score"]
        print(
            f"{name:<14} {q.get('cross_file_edge_pct', 0):>6.1f} "
            f"{q.get('symbol_reachability_pct', 0):>6.1f} "
            f"{q.get('qualified_name_pct', 0):>5.1f} "
            f"{q.get('orphan_file_pct', 0):>7.1f} "
            f"{q['dead_high_conf']:>5} "
            f"{q['hidden_coupling_pct']:>7.1f} {s:>6.2f}"
        )
    print()


def print_language_summary(results):
    """Average scores grouped by language."""
    by_lang = {}
    for name, data in results.items():
        if data is None:
            continue
        lang = REPOS[name]["lang"]
        by_lang.setdefault(lang, []).append((name, data))

    print("Per-language summary:")
    print(f"  {'Lang':<6} {'Repos':>5} {'Avg Score':>10} {'Avg Cov%':>9} "
          f"{'Avg MisR%':>10} {'Avg E/S':>8}")
    print(f"  {'-'*50}")
    for lang in sorted(by_lang.keys()):
        entries = by_lang[lang]
        n = len(entries)
        avg_score = sum(d["score"] for _, d in entries) / n
        avg_cov = sum(d["quality"]["symbol_coverage_pct"] for _, d in entries) / n
        avg_misres = sum(d["quality"]["misres_rate_pct"] for _, d in entries) / n
        avg_ed = sum(d["quality"]["edge_density"] for _, d in entries) / n
        repo_names = ", ".join(nm for nm, _ in entries)
        print(f"  {lang:<6} {n:>5} {avg_score:>10.2f} {avg_cov:>9.1f} "
              f"{avg_misres:>10.2f} {avg_ed:>8.2f}  ({repo_names})")
    print()


def print_delta(results, baseline):
    """Compare current results against a baseline JSON."""
    base_repos = baseline.get("repos", {})
    print("\n=== DELTA vs BASELINE ===")
    base_date = baseline.get("date", "?")
    print(f"Baseline: {base_date}")
    print(f"{'Repo':<14} {'Base':>6} {'Now':>6} {'Delta':>7}  Note")
    print("-" * 55)

    deltas = []
    for name in sorted(results.keys()):
        cur = results[name]
        if cur is None:
            continue
        cur_score = cur["score"]
        base = base_repos.get(name)
        if base is None:
            print(f"{name:<14} {'n/a':>6} {cur_score:>6.2f} {'new':>7}")
            continue
        base_score = base.get("score", 0)
        d = cur_score - base_score
        deltas.append(d)
        marker = ""
        if d > 1.0:
            marker = "++ major improvement"
        elif d < -1.0:
            marker = "!! MAJOR REGRESSION"
        elif d > 0.1:
            marker = "+ improved"
        elif d < -0.1:
            marker = "- regressed"
        print(f"{name:<14} {base_score:>6.2f} {cur_score:>6.2f} {d:>+7.2f}  {marker}")

    if deltas:
        avg_d = sum(deltas) / len(deltas)
        print("-" * 55)
        print(f"{'OVERALL':<14} {'':>6} {'':>6} {avg_d:>+7.2f}")
    print()


def save_json(results, output_path, repo_names, wall_time):
    total_pass = sum(
        r["commands"]["passed"] for r in results.values() if r
    )
    total_fail = sum(
        r["commands"]["failed"] for r in results.values() if r
    )
    scores = [r["score"] for r in results.values() if r]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0

    doc = {
        "version": "3.8",
        "date": datetime.now().isoformat(timespec="seconds"),
        "roam_commit": roam_commit(),
        "wall_time_s": round(wall_time, 1),
        "repos": results,
        "summary": {
            "avg_score": avg_score,
            "total_cmd_pass": total_pass,
            "total_cmd_fail": total_fail,
            "repos_run": len(repo_names),
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Results saved to {output_path}")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    wall_t0 = time.time()

    parser = argparse.ArgumentParser(description="Roam benchmark suite")
    parser.add_argument("--repos", help="Comma-separated repo subset")
    parser.add_argument("--skip-slow", action="store_true",
                        help="Skip django, spring-boot, nextjs")
    parser.add_argument("--skip-clone", action="store_true",
                        help="Skip git clone/update phase")
    parser.add_argument("--skip-index", action="store_true",
                        help="Skip indexing phase (use existing DBs)")
    parser.add_argument("--skip-commands", action="store_true",
                        help="Skip command validation phase")
    parser.add_argument("--baseline", help="Path to previous JSON for delta comparison")
    parser.add_argument("--output", help="Output JSON path",
                        default=f"bench-results-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")

    args = parser.parse_args()

    # Determine repo list
    if args.repos:
        names = [n.strip() for n in args.repos.split(",")]
        bad = [n for n in names if n not in REPOS]
        if bad:
            print(f"Unknown repos: {', '.join(bad)}")
            print(f"Available: {', '.join(sorted(REPOS))}")
            sys.exit(1)
    else:
        names = list(REPOS.keys())

    if args.skip_slow:
        names = [n for n in names if n not in SLOW_REPOS]

    print(f"{'=' * 65}")
    print(f"  ROAM BENCHMARK - v3.8")
    print(f"  {len(names)} repos: {', '.join(names)}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 65}")
    print()

    # Phase 1: Clone
    if not args.skip_clone:
        print("Phase 1: SETUP (git clone --depth 1)")
        print("-" * 40)
        setup_repos(names)
        print()

    # Phase 2: Index
    index_times = {}
    if not args.skip_index:
        print("Phase 2: INDEX (roam index --force)")
        print("-" * 40)
        for i, name in enumerate(names, 1):
            t = index_repo(name, i, len(names))
            index_times[name] = t
        print()
    else:
        print("Phase 2: INDEX - skipped (using existing DBs)")
        print()
        for name in names:
            index_times[name] = None

    # Phase 3+4: Measure + Validate
    print("Phase 3+4: MEASURE & VALIDATE")
    print("-" * 40)
    results = {}
    for i, name in enumerate(names, 1):
        print(f"\n  [{i}/{len(names)}] {name}")
        print(f"  {'-' * 30}")
        data = measure(name, index_times.get(name))
        if data is None:
            print(f"    SKIPPED (no DB)")
            results[name] = None
            continue

        # Print quick measure summary
        q = data["quality"]
        idx = data["index"]
        print(f"    {idx['files']} files, {idx['symbols']} symbols, "
              f"{idx['edges']} edges, coverage {q['symbol_coverage_pct']:.1f}%")

        # Validate commands
        if not args.skip_commands:
            cmd_results = validate_commands(name)
            data["commands"] = cmd_results
        else:
            data["commands"] = {"total": len(ALL_COMMANDS), "passed": len(ALL_COMMANDS),
                                "failed": 0, "failures": [], "timings": {},
                                "sampled_args": {}}

        # Composite score
        sub_scores, _, composite = compute_sub_scores(data["quality"], data["commands"])
        data["score"] = composite
        data["sub_scores"] = sub_scores
        data["head_commit"] = head_commit(BENCH_DIR / name)
        results[name] = data

    wall_time = time.time() - wall_t0

    # Phase 5: Report
    print(f"\n{'=' * 65}")
    print(f"  RESULTS")
    print(f"{'=' * 65}")

    # Per-repo detail cards
    for name in names:
        if results.get(name):
            print_repo_card(name, results[name])

    # Compact comparison table
    print(f"\n{'=' * 65}")
    print(f"  COMPARISON TABLE")
    print(f"{'=' * 65}")
    print_table(results)

    # Enriched quality metrics
    print_enriched_table(results)

    # Per-language summary
    print_language_summary(results)

    # Slowest commands across all repos
    all_timings = []
    for rname, data in results.items():
        if data and "timings" in data.get("commands", {}):
            for cmd, t in data["commands"]["timings"].items():
                if t > 0:
                    all_timings.append((t, rname, cmd))
    if all_timings:
        all_timings.sort(reverse=True)
        print("Top 15 slowest commands:")
        for t, rname, cmd in all_timings[:15]:
            print(f"  {t:>6.1f}s  {rname}/{cmd}")
        print()

    # Save JSON
    save_json(results, args.output, names, wall_time)

    # Delta comparison
    if args.baseline:
        try:
            with open(args.baseline, "r", encoding="utf-8") as f:
                baseline = json.load(f)
            print_delta(results, baseline)
        except FileNotFoundError:
            print(f"Baseline file not found: {args.baseline}")
        except json.JSONDecodeError:
            print(f"Invalid JSON in baseline: {args.baseline}")

    # Final banner
    scores = [r["score"] for r in results.values() if r]
    if scores:
        avg = sum(scores) / len(scores)
        fails = sum(r["commands"]["failed"] for r in results.values() if r)
        total_cmds = sum(r["commands"]["total"] for r in results.values() if r)
        print(f"{'=' * 65}")
        print(f"  DONE in {fmt_duration(wall_time)}")
        print(f"  Average score: {avg:.2f}/10")
        print(f"  Commands: {total_cmds - fails}/{total_cmds} passed")
        if fails:
            print(f"  FAILURES: {fails}")
        print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
