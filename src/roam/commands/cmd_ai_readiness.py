"""Estimate how effectively AI agents can work on this codebase (0-100).

The ai-readiness command scores 7 dimensions that affect AI agent
effectiveness:

1. Naming consistency   (15%) -- well-named code is easier for AI to comprehend
2. Module coupling      (20%) -- low coupling lets agents work independently
3. Dead code noise      (15%) -- dead code wastes context and confuses agents
4. Test signal strength (20%) -- tests let agents verify their changes
5. Documentation signal (10%) -- docs help agents understand intent
6. Codebase navigability(10%) -- small files and flat structure = navigable
7. Architecture clarity (10%) -- clear layers = agents understand boundaries

Produces a composite 0-100 AI Readiness Score and per-dimension breakdown.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import format_table, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Severity labels
# ---------------------------------------------------------------------------

def _readiness_label(score: int) -> str:
    if score <= 25:
        return "HOSTILE"
    elif score <= 45:
        return "POOR"
    elif score <= 65:
        return "FAIR"
    elif score <= 80:
        return "GOOD"
    else:
        return "OPTIMIZED"


# ---------------------------------------------------------------------------
# Naming convention patterns per language
# ---------------------------------------------------------------------------

_SNAKE_CASE = re.compile(r'^[a-z_][a-z0-9_]*$')
_CAMEL_CASE = re.compile(r'^[a-z][a-zA-Z0-9]*$')
_PASCAL_CASE = re.compile(r'^[A-Z][a-zA-Z0-9]*$')
_UPPER_SNAKE = re.compile(r'^[A-Z_][A-Z0-9_]*$')
# Go exported: PascalCase; unexported: camelCase or snake_case
_GO_EXPORTED = re.compile(r'^[A-Z][a-zA-Z0-9]*$')
_GO_UNEXPORTED = re.compile(r'^[a-z][a-zA-Z0-9_]*$')

# Language -> (function/method pattern, class pattern)
_NAMING_CONVENTIONS: dict[str, dict[str, re.Pattern]] = {
    "python": {
        "function": _SNAKE_CASE,
        "method": _SNAKE_CASE,
        "class": _PASCAL_CASE,
        "variable": _SNAKE_CASE,
        "constant": _UPPER_SNAKE,
    },
    "javascript": {
        "function": _CAMEL_CASE,
        "method": _CAMEL_CASE,
        "class": _PASCAL_CASE,
        "variable": _CAMEL_CASE,
        "constant": _UPPER_SNAKE,
    },
    "typescript": {
        "function": _CAMEL_CASE,
        "method": _CAMEL_CASE,
        "class": _PASCAL_CASE,
        "interface": _PASCAL_CASE,
        "variable": _CAMEL_CASE,
        "constant": _UPPER_SNAKE,
    },
    "java": {
        "function": _CAMEL_CASE,
        "method": _CAMEL_CASE,
        "class": _PASCAL_CASE,
        "variable": _CAMEL_CASE,
        "constant": _UPPER_SNAKE,
    },
    "go": {
        "function": _GO_UNEXPORTED,
        "method": _GO_UNEXPORTED,
        "class": _GO_EXPORTED,  # struct
        "variable": _GO_UNEXPORTED,
        "constant": _GO_UNEXPORTED,
    },
    "ruby": {
        "function": _SNAKE_CASE,
        "method": _SNAKE_CASE,
        "class": _PASCAL_CASE,
        "variable": _SNAKE_CASE,
        "constant": _UPPER_SNAKE,
    },
    "rust": {
        "function": _SNAKE_CASE,
        "method": _SNAKE_CASE,
        "class": _PASCAL_CASE,  # struct
        "variable": _SNAKE_CASE,
        "constant": _UPPER_SNAKE,
    },
    "c_sharp": {
        "function": _PASCAL_CASE,
        "method": _PASCAL_CASE,
        "class": _PASCAL_CASE,
        "variable": _CAMEL_CASE,
        "constant": _PASCAL_CASE,
    },
    "php": {
        "function": _CAMEL_CASE,
        "method": _CAMEL_CASE,
        "class": _PASCAL_CASE,
        "variable": _CAMEL_CASE,
        "constant": _UPPER_SNAKE,
    },
}

# Names that should be excluded from naming checks (framework/dunder/special)
_NAMING_EXCLUDE = re.compile(
    r'^(__.*__|setUp|tearDown|setUpClass|tearDownClass|'
    r'constructor|toString|valueOf|toJSON|main|init|'
    r'New|Close|String|Error|fmt|from|into|drop|new|default)$'
)


# ---------------------------------------------------------------------------
# Dimension 1: Naming consistency
# ---------------------------------------------------------------------------

def _score_naming(conn) -> tuple[int, dict]:
    """Score naming convention consistency (0-100).

    Measures % of symbols following language-specific naming conventions.
    """
    rows = conn.execute(
        "SELECT s.name, s.kind, f.language "
        "FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE f.language IS NOT NULL "
        "AND s.kind IN ('function', 'method', 'class', 'variable', 'constant', "
        "  'interface', 'struct', 'enum')"
    ).fetchall()

    if not rows:
        return 100, {"checked": 0, "conforming": 0, "total": 0}

    checked = 0
    conforming = 0

    for r in rows:
        name = r["name"]
        kind = r["kind"]
        lang = r["language"]

        # Skip special/framework names
        if _NAMING_EXCLUDE.match(name):
            continue

        conventions = _NAMING_CONVENTIONS.get(lang)
        if not conventions:
            continue

        # Map struct/enum to class for convention lookup
        lookup_kind = kind
        if kind in ("struct", "enum"):
            lookup_kind = "class"
        if kind == "interface":
            lookup_kind = "interface" if "interface" in conventions else "class"

        pattern = conventions.get(lookup_kind)
        if not pattern:
            continue

        checked += 1
        if pattern.match(name):
            conforming += 1

    if checked == 0:
        return 100, {"checked": 0, "conforming": 0, "total": len(rows)}

    rate = conforming / checked
    score = max(0, min(100, int(round(rate * 100))))
    return score, {
        "checked": checked,
        "conforming": conforming,
        "total": len(rows),
        "rate": round(rate * 100, 1),
    }


# ---------------------------------------------------------------------------
# Dimension 2: Module coupling (via tangle ratio)
# ---------------------------------------------------------------------------

def _score_coupling(conn) -> tuple[int, dict]:
    """Score module coupling (0-100). Lower tangle ratio = better.

    Uses tangle_ratio: % of symbols involved in dependency cycles.
    tangle_ratio = 0 -> score 100, tangle_ratio >= 0.5 -> score 0.
    """
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        if len(G) == 0:
            return 100, {"tangle_ratio": 0.0, "symbols_in_cycles": 0,
                         "total_symbols": 0, "cycle_count": 0}

        cycles = find_cycles(G)
        total_symbols = len(G)
        cycle_symbol_ids = set()
        for scc in cycles:
            cycle_symbol_ids.update(scc)

        tangle_ratio = len(cycle_symbol_ids) / max(total_symbols, 1)

        # Linear mapping: 0 -> 100, 0.5+ -> 0
        score = max(0, min(100, int(round(100 * (1 - tangle_ratio / 0.5)))))

        return score, {
            "tangle_ratio": round(tangle_ratio * 100, 1),
            "symbols_in_cycles": len(cycle_symbol_ids),
            "total_symbols": total_symbols,
            "cycle_count": len(cycles),
        }
    except Exception:
        return 50, {"tangle_ratio": 0.0, "error": "graph build failed"}


# ---------------------------------------------------------------------------
# Dimension 3: Dead code noise
# ---------------------------------------------------------------------------

def _score_dead_code(conn) -> tuple[int, dict]:
    """Score dead code noise (0-100). Less dead code = better.

    Measures % of exported symbols with zero incoming edges (dead exports),
    excluding test files, cmd_ files, dunders.
    """
    _EXCLUDE_SQL = (
        "AND f.path NOT LIKE '%test\\_%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%\\_test.%' ESCAPE '\\' "
        "AND f.path NOT LIKE '%/tests/%' "
        "AND f.path NOT LIKE '%/test/%' "
        "AND f.path NOT LIKE '%conftest%' "
        "AND f.path NOT LIKE '%cmd\\_%' ESCAPE '\\' "
    )

    total = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        + _EXCLUDE_SQL
    ).fetchone()[0]

    dead = conn.execute(
        "SELECT COUNT(*) FROM symbols s "
        "JOIN files f ON s.file_id = f.id "
        "WHERE s.kind IN ('function', 'class', 'method') "
        "AND s.name NOT LIKE '\\_%' ESCAPE '\\' "
        "AND s.is_exported = 1 "
        "AND s.id NOT IN (SELECT target_id FROM edges) "
        + _EXCLUDE_SQL
    ).fetchone()[0]

    total = max(total, 1)
    dead_rate = dead / total

    # 0% dead = 100, 20%+ dead = 0
    score = max(0, min(100, int(round(100 * (1 - dead_rate / 0.20)))))

    return score, {
        "dead_exports": dead,
        "total_exports": total,
        "dead_rate": round(dead_rate * 100, 1),
    }


# ---------------------------------------------------------------------------
# Dimension 4: Test signal strength
# ---------------------------------------------------------------------------

def _score_test_signal(conn) -> tuple[int, dict]:
    """Score test coverage mapping (0-100).

    Measures % of source files that have a matching test file.
    """
    from roam.index.test_conventions import get_conventions

    # Get all source files
    source_files = conn.execute(
        "SELECT path, language FROM files "
        "WHERE file_role = 'source' AND language IS NOT NULL"
    ).fetchall()

    if not source_files:
        return 50, {"source_files": 0, "with_tests": 0, "coverage_rate": 0.0}

    # Get all file paths for quick lookup
    all_paths = set()
    for r in conn.execute("SELECT path FROM files").fetchall():
        all_paths.add(r["path"].replace("\\", "/"))

    conventions = get_conventions()
    source_count = 0
    with_test = 0

    for f in source_files:
        path = f["path"].replace("\\", "/")
        lang = f["language"]

        # Skip test files that were misclassified as source
        basename = os.path.basename(path)
        if basename.startswith("test_") or basename.endswith("_test.py"):
            continue
        if ".test." in basename or ".spec." in basename:
            continue

        source_count += 1

        # Check each convention for a matching test file
        found_test = False
        for conv in conventions:
            if lang and lang not in conv.languages:
                continue
            candidates = conv.source_to_test_paths(path)
            for candidate in candidates:
                if candidate.replace("\\", "/") in all_paths:
                    found_test = True
                    break
            if found_test:
                break

        if found_test:
            with_test += 1

    source_count = max(source_count, 1)
    coverage_rate = with_test / source_count
    score = max(0, min(100, int(round(coverage_rate * 100))))

    return score, {
        "source_files": source_count,
        "with_tests": with_test,
        "coverage_rate": round(coverage_rate * 100, 1),
    }


# ---------------------------------------------------------------------------
# Dimension 5: Documentation signal
# ---------------------------------------------------------------------------

def _score_documentation(conn, project_root: Path) -> tuple[int, dict]:
    """Score documentation quality (0-100).

    Checks: README presence, CLAUDE.md/AGENTS.md presence,
    function docstring ratio.
    """
    points = 0
    max_points = 100
    details: dict = {}

    # Check for README (30 points)
    has_readme = False
    for name in ("README.md", "README.rst", "README.txt", "README",
                 "readme.md", "Readme.md"):
        if (project_root / name).exists():
            has_readme = True
            break
    if has_readme:
        points += 30
    details["has_readme"] = has_readme

    # Check for AI agent docs (20 points)
    has_agent_doc = False
    for name in ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", ".github/copilot-instructions.md",
                 ".cursorrules", ".clinerules"):
        if (project_root / name).exists():
            has_agent_doc = True
            break
    if has_agent_doc:
        points += 20
    details["has_agent_doc"] = has_agent_doc

    # Docstring ratio for functions/methods (50 points)
    total_fns = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE kind IN ('function', 'method')"
    ).fetchone()[0]

    fns_with_docstring = conn.execute(
        "SELECT COUNT(*) FROM symbols "
        "WHERE kind IN ('function', 'method') "
        "AND docstring IS NOT NULL AND docstring != ''"
    ).fetchone()[0]

    if total_fns > 0:
        docstring_rate = fns_with_docstring / total_fns
        points += int(round(docstring_rate * 50))
        details["docstring_rate"] = round(docstring_rate * 100, 1)
    else:
        details["docstring_rate"] = 0.0

    details["total_functions"] = total_fns
    details["functions_with_docstrings"] = fns_with_docstring

    score = max(0, min(100, int(round(points / max_points * 100))))
    return score, details


# ---------------------------------------------------------------------------
# Dimension 6: Codebase navigability
# ---------------------------------------------------------------------------

def _score_navigability(conn) -> tuple[int, dict]:
    """Score codebase navigability (0-100).

    Measures average file size, max directory depth, files per directory.
    Ideal: avg <300 lines, max depth <6, <20 files/dir.
    """
    files = conn.execute(
        "SELECT path, line_count FROM files WHERE line_count > 0"
    ).fetchall()

    if not files:
        return 100, {"avg_lines": 0, "max_depth": 0, "max_files_per_dir": 0,
                     "file_count": 0}

    # Average file size score (40 points)
    line_counts = [f["line_count"] for f in files]
    avg_lines = sum(line_counts) / len(line_counts)

    # avg_lines <= 300 -> 40 pts, >= 1000 -> 0 pts
    if avg_lines <= 300:
        size_points = 40
    elif avg_lines >= 1000:
        size_points = 0
    else:
        size_points = int(round(40 * (1 - (avg_lines - 300) / 700)))

    # Max directory depth score (30 points)
    max_depth = 0
    dir_counts: dict[str, int] = {}
    for f in files:
        path = f["path"].replace("\\", "/")
        parts = path.split("/")
        depth = len(parts) - 1  # exclude filename
        if depth > max_depth:
            max_depth = depth

        # Count files per directory
        dir_path = "/".join(parts[:-1]) if len(parts) > 1 else "."
        dir_counts[dir_path] = dir_counts.get(dir_path, 0) + 1

    # depth <= 5 -> 30 pts, >= 10 -> 0 pts
    if max_depth <= 5:
        depth_points = 30
    elif max_depth >= 10:
        depth_points = 0
    else:
        depth_points = int(round(30 * (1 - (max_depth - 5) / 5)))

    # Files per directory score (30 points)
    max_files_per_dir = max(dir_counts.values()) if dir_counts else 0

    # <= 20 files/dir -> 30 pts, >= 50 -> 0 pts
    if max_files_per_dir <= 20:
        density_points = 30
    elif max_files_per_dir >= 50:
        density_points = 0
    else:
        density_points = int(round(30 * (1 - (max_files_per_dir - 20) / 30)))

    total_points = size_points + depth_points + density_points
    score = max(0, min(100, total_points))

    return score, {
        "avg_lines": round(avg_lines, 1),
        "max_depth": max_depth,
        "max_files_per_dir": max_files_per_dir,
        "file_count": len(files),
        "directory_count": len(dir_counts),
    }


# ---------------------------------------------------------------------------
# Dimension 7: Architecture clarity
# ---------------------------------------------------------------------------

def _score_architecture(conn) -> tuple[int, dict]:
    """Score architecture clarity (0-100).

    Measures layer violations and cycle count.
    0 violations + 0 cycles = 100.
    """
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles
        from roam.graph.layers import detect_layers, find_violations

        G = build_symbol_graph(conn)
        if len(G) == 0:
            return 100, {"violations": 0, "cycles": 0}

        cycles = find_cycles(G)
        layer_map = detect_layers(G)
        violations = find_violations(G, layer_map) if layer_map else []

        # Violations penalty: each violation costs 5 pts, up to 50
        violation_penalty = min(50, len(violations) * 5)
        # Cycles penalty: each cycle costs 10 pts, up to 50
        cycle_penalty = min(50, len(cycles) * 10)

        score = max(0, 100 - violation_penalty - cycle_penalty)

        return score, {
            "violations": len(violations),
            "cycles": len(cycles),
            "layers_detected": len(set(layer_map.values())) if layer_map else 0,
        }
    except Exception:
        return 50, {"violations": 0, "cycles": 0, "error": "graph build failed"}


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

_WEIGHTS = {
    "naming_consistency": 15,
    "module_coupling": 20,
    "dead_code_noise": 15,
    "test_signal_strength": 20,
    "documentation_signal": 10,
    "codebase_navigability": 10,
    "architecture_clarity": 10,
}

_DIMENSION_LABELS = {
    "naming_consistency": "Naming consistency",
    "module_coupling": "Module coupling",
    "dead_code_noise": "Dead code noise",
    "test_signal_strength": "Test signal strength",
    "documentation_signal": "Documentation signal",
    "codebase_navigability": "Codebase navigability",
    "architecture_clarity": "Architecture clarity",
}


def _compute_composite(dimensions: dict[str, int]) -> int:
    """Compute weighted composite AI Readiness Score (0-100)."""
    weighted_sum = 0.0
    total_weight = sum(_WEIGHTS.values())

    for key, weight in _WEIGHTS.items():
        dim_score = dimensions.get(key, 0)
        weighted_sum += dim_score * weight

    score = weighted_sum / total_weight
    return max(0, min(100, int(round(score))))


# ---------------------------------------------------------------------------
# Recommendations generator
# ---------------------------------------------------------------------------

def _generate_recommendations(dimensions: dict[str, int],
                               details: dict[str, dict]) -> list[str]:
    """Generate actionable recommendations from dimension scores."""
    recs: list[str] = []

    # Sort dimensions by score (lowest first) for priority
    sorted_dims = sorted(dimensions.items(), key=lambda x: x[1])

    for key, score in sorted_dims:
        if score >= 80:
            continue

        if key == "test_signal_strength":
            d = details.get(key, {})
            rate = d.get("coverage_rate", 0)
            recs.append(
                f"Increase test coverage mapping (currently {rate:.0f}%)"
            )
        elif key == "module_coupling":
            d = details.get(key, {})
            tr = d.get("tangle_ratio", 0)
            recs.append(
                f"Reduce module coupling (tangle ratio: {tr:.1f}%)"
            )
        elif key == "dead_code_noise":
            d = details.get(key, {})
            dead = d.get("dead_exports", 0)
            if dead > 0:
                recs.append(
                    f"Remove {dead} dead exports to reduce agent confusion"
                )
        elif key == "naming_consistency":
            d = details.get(key, {})
            rate = d.get("rate", 0)
            recs.append(
                f"Improve naming consistency (currently {rate:.0f}% conforming)"
            )
        elif key == "documentation_signal":
            d = details.get(key, {})
            if not d.get("has_readme"):
                recs.append("Add a README file")
            if not d.get("has_agent_doc"):
                recs.append(
                    "Add CLAUDE.md or AGENTS.md for AI agent guidance"
                )
            dr = d.get("docstring_rate", 0)
            if dr < 50:
                recs.append(
                    f"Add docstrings to more functions (currently {dr:.0f}%)"
                )
        elif key == "codebase_navigability":
            d = details.get(key, {})
            avg = d.get("avg_lines", 0)
            if avg > 300:
                recs.append(
                    f"Reduce average file size ({avg:.0f} lines, target <300)"
                )
            depth = d.get("max_depth", 0)
            if depth > 5:
                recs.append(
                    f"Flatten directory structure (max depth: {depth}, target <6)"
                )
        elif key == "architecture_clarity":
            d = details.get(key, {})
            v = d.get("violations", 0)
            c = d.get("cycles", 0)
            if v > 0:
                recs.append(
                    f"Fix {v} layer violation{'s' if v != 1 else ''}"
                )
            if c > 0:
                recs.append(
                    f"Break {c} dependency cycle{'s' if c != 1 else ''}"
                )

    return recs[:5]  # Cap at 5 recommendations


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("ai-readiness")
@click.option("--threshold", type=int, default=0,
              help="Fail if AI readiness score is below threshold (0=no gate)")
@click.pass_context
def ai_readiness(ctx, threshold):
    """Estimate how effectively AI agents can work on this codebase (0-100)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        # Score all 7 dimensions
        naming_score, naming_details = _score_naming(conn)
        coupling_score, coupling_details = _score_coupling(conn)
        dead_score, dead_details = _score_dead_code(conn)
        test_score, test_details = _score_test_signal(conn)
        doc_score, doc_details = _score_documentation(conn, project_root)
        nav_score, nav_details = _score_navigability(conn)
        arch_score, arch_details = _score_architecture(conn)

        dimensions = {
            "naming_consistency": naming_score,
            "module_coupling": coupling_score,
            "dead_code_noise": dead_score,
            "test_signal_strength": test_score,
            "documentation_signal": doc_score,
            "codebase_navigability": nav_score,
            "architecture_clarity": arch_score,
        }

        all_details = {
            "naming_consistency": naming_details,
            "module_coupling": coupling_details,
            "dead_code_noise": dead_details,
            "test_signal_strength": test_details,
            "documentation_signal": doc_details,
            "codebase_navigability": nav_details,
            "architecture_clarity": arch_details,
        }

        composite = _compute_composite(dimensions)
        label = _readiness_label(composite)
        recommendations = _generate_recommendations(dimensions, all_details)

        verdict = f"AI Readiness {composite}/100 -- {label}"

        files_scanned = conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]

        # --- JSON output ---
        if json_mode:
            dim_list = []
            for key in _WEIGHTS:
                dim_list.append({
                    "name": key,
                    "label": _DIMENSION_LABELS[key],
                    "score": dimensions[key],
                    "weight": _WEIGHTS[key],
                    "contribution": round(
                        dimensions[key] * _WEIGHTS[key] / 100, 1
                    ),
                    "details": all_details[key],
                })

            envelope = json_envelope("ai-readiness",
                budget=budget,
                summary={
                    "verdict": verdict,
                    "score": composite,
                    "label": label,
                    "files_scanned": files_scanned,
                },
                dimensions=dim_list,
                recommendations=recommendations,
            )
            click.echo(to_json(envelope))

            # Gate check (below threshold = fail)
            if threshold > 0 and composite < threshold:
                from roam.exit_codes import EXIT_GATE_FAILURE
                ctx.exit(EXIT_GATE_FAILURE)
            return

        # --- Text output ---
        click.echo(f"VERDICT: {verdict}")
        click.echo()

        # Dimension table
        headers = ["Dimension", "Score", "Weight", "Contribution"]
        rows = []
        for key in _WEIGHTS:
            score = dimensions[key]
            weight = _WEIGHTS[key]
            contribution = round(score * weight / 100, 1)
            rows.append([
                _DIMENSION_LABELS[key],
                str(score),
                f"{weight}%",
                str(contribution),
            ])

        click.echo(format_table(headers, rows))
        click.echo()
        click.echo(f"  {composite}/100 AI Readiness "
                   f"(0=hostile, 100=optimized)")

        # Recommendations
        if recommendations:
            click.echo()
            click.echo("  Recommendations:")
            for rec in recommendations:
                click.echo(f"    - {rec}")

        # Gate check (below threshold = fail)
        if threshold > 0 and composite < threshold:
            click.echo()
            click.echo(
                f"  GATE FAILED: score {composite} below threshold {threshold}"
            )
            from roam.exit_codes import EXIT_GATE_FAILURE
            ctx.exit(EXIT_GATE_FAILURE)
