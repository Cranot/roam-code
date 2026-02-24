"""Orchestrates the full indexing pipeline."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from roam.db.connection import open_db, find_project_root, get_db_path
from roam.index.discovery import discover_files
from roam.index.parser import parse_file, detect_language, extract_vue_template, scan_template_references
from roam.index.symbols import extract_symbols, extract_references
from roam.index.relations import resolve_references, build_file_edges
from roam.index.incremental import get_changed_files, file_hash
from roam.languages.generic_lang import GenericExtractor
from roam.index.file_roles import classify_file


def _format_count(n: int) -> str:
    """Format an integer with thousands separators."""
    return f"{n:,}"


def _compute_complexity(source: bytes) -> float:
    """Compute a simple indentation-based complexity metric.

    Returns average_depth * max_depth for non-blank lines.
    """
    lines = source.split(b"\n")
    depths = []
    for line in lines:
        expanded = line.expandtabs(4)
        stripped = expanded.lstrip()
        if not stripped:
            continue
        indent = len(expanded) - len(stripped)
        depths.append(indent / 4.0)  # Normalise to "levels"

    if not depths:
        return 0.0
    avg = sum(depths) / len(depths)
    mx = max(depths)
    return round(avg * mx, 2)


def _count_lines(source: bytes) -> int:
    return source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)


def _try_import_get_extractor():
    """Try to import the language extractor registry."""
    try:
        from roam.languages.registry import get_extractor
        return get_extractor
    except ImportError:
        return None


def _try_import_graph():
    """Try to import graph computation modules."""
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.pagerank import store_metrics
        from roam.graph.clusters import detect_clusters, label_clusters, store_clusters
        return build_symbol_graph, store_metrics, detect_clusters, label_clusters, store_clusters
    except ImportError:
        return None, None, None, None, None


def _try_import_complexity():
    """Try to import symbol complexity module."""
    try:
        from roam.index.complexity import compute_and_store
        return compute_and_store
    except ImportError:
        return None


def _try_import_git_stats():
    """Try to import git stats module."""
    try:
        from roam.index.git_stats import collect_git_stats
        return collect_git_stats
    except ImportError:
        return None


def _try_import_effects():
    """Try to import the effect classification module."""
    try:
        from roam.analysis.effects import compute_and_store_effects
        return compute_and_store_effects
    except ImportError:
        return None


_quiet_mode = False


def _log(msg: str):
    if not _quiet_mode:
        print(msg, file=sys.stderr, flush=True)


def _compute_file_health_scores(conn, G=None):
    """Compute a 1-10 health score for every file, fusing all signals.

    Factors (CodeScene-inspired composite):
    1. Max cognitive complexity of any function in the file (brain method)
    2. File-level indentation complexity
    3. Cycle membership (any symbol in a cycle?)
    4. God component membership (any symbol with degree > 20?)
    5. Dead export ratio
    6. Co-change entropy (high = shotgun surgery)
    7. Churn-weighted amplification (high churn + low health = worse)

    Score: 10 = healthy, 1 = critical. Stored in file_stats.health_score.

    *G* is an optional pre-built NetworkX DiGraph.  When supplied, cycle
    membership is determined via Tarjan SCC (``nx.strongly_connected_components``),
    which correctly identifies ALL cycle lengths in O(V+E).  When *G* is
    ``None`` the function falls back to a SQL 2-cycle self-join heuristic
    (only catches A→B→A patterns) for backwards compatibility.
    """
    # Gather per-file data
    files = conn.execute("SELECT id, path FROM files").fetchall()
    if not files:
        return

    # Max complexity per file
    max_cc_by_file = {}
    rows = conn.execute(
        "SELECT s.file_id, MAX(sm.cognitive_complexity) as max_cc "
        "FROM symbol_metrics sm JOIN symbols s ON s.id = sm.symbol_id "
        "GROUP BY s.file_id"
    ).fetchall()
    for r in rows:
        max_cc_by_file[r["file_id"]] = r["max_cc"] or 0

    # Cycle membership: which files have symbols in cycles?
    # Prefer SCC-based detection (all cycle lengths, O(V+E)) when the graph
    # is available; fall back to the SQL 2-cycle self-join only as a last resort.
    cycle_files: set = set()
    if G is not None:
        try:
            from roam.graph.cycles import find_cycles
            sccs = find_cycles(G, min_size=2)
            # Collect every symbol ID that participates in any SCC (cycle of any length)
            cycle_symbol_ids: set = set()
            for scc in sccs:
                cycle_symbol_ids.update(scc)
            if cycle_symbol_ids:
                from roam.db.connection import batched_in
                rows_cyc = batched_in(
                    conn,
                    "SELECT DISTINCT file_id FROM symbols WHERE id IN ({ph})",
                    list(cycle_symbol_ids),
                )
                cycle_files = {r[0] for r in rows_cyc}
        except Exception:
            pass
    else:
        # Fallback: SQL 2-cycle self-join (only catches A→B→A patterns)
        try:
            cycle_rows = conn.execute(
                "SELECT DISTINCT s.file_id FROM symbols s "
                "JOIN edges e1 ON e1.source_id = s.id "
                "JOIN edges e2 ON e2.target_id = s.id "
                "WHERE e1.target_id IN (SELECT source_id FROM edges WHERE target_id = s.id)"
            ).fetchall()
            cycle_files = {r["file_id"] for r in cycle_rows}
        except Exception:
            pass

    # God components: files with symbols having degree > 20
    god_files = set()
    try:
        god_rows = conn.execute(
            "SELECT DISTINCT s.file_id FROM symbols s "
            "JOIN graph_metrics gm ON gm.symbol_id = s.id "
            "WHERE (gm.in_degree + gm.out_degree) > 20"
        ).fetchall()
        god_files = {r["file_id"] for r in god_rows}
    except Exception:
        pass

    # Dead exports per file
    dead_by_file = {}
    try:
        dead_rows = conn.execute(
            "SELECT s.file_id, "
            "COUNT(*) as total_exports, "
            "SUM(CASE WHEN gm.in_degree = 0 THEN 1 ELSE 0 END) as dead "
            "FROM symbols s "
            "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
            "WHERE s.is_exported = 1 "
            "GROUP BY s.file_id"
        ).fetchall()
        for r in dead_rows:
            total = r["total_exports"] or 1
            dead = r["dead"] or 0
            dead_by_file[r["file_id"]] = dead / total
    except Exception:
        pass

    # File stats (churn, complexity, entropy)
    stats = {}
    stat_rows = conn.execute(
        "SELECT file_id, total_churn, complexity, cochange_entropy "
        "FROM file_stats"
    ).fetchall()
    for r in stat_rows:
        stats[r["file_id"]] = {
            "churn": r["total_churn"] or 0,
            "complexity": r["complexity"] or 0,
            "entropy": r["cochange_entropy"],
        }

    # Compute churn percentiles for amplification
    churns = sorted(s["churn"] for s in stats.values() if s["churn"] > 0)
    if churns:
        n = len(churns)
        k50 = (n - 1) * 0.5
        churn_p50 = churns[int(k50)] + (k50 - int(k50)) * (churns[min(int(k50) + 1, n - 1)] - churns[int(k50)])
        k90 = (n - 1) * 0.9
        churn_p90 = churns[int(k90)] + (k90 - int(k90)) * (churns[min(int(k90) + 1, n - 1)] - churns[int(k90)])
    else:
        churn_p50, churn_p90 = 1, 1

    # Compute health score per file
    updates = []
    for f in files:
        fid = f["id"]
        score = 10.0  # Start at perfect health

        # Factor 1: Max cognitive complexity (0 to -4 points)
        max_cc = max_cc_by_file.get(fid, 0)
        if max_cc >= 40:
            score -= 4.0
        elif max_cc >= 25:
            score -= 3.0
        elif max_cc >= 15:
            score -= 2.0
        elif max_cc >= 8:
            score -= 1.0

        # Factor 2: File-level complexity (0 to -1.5 points)
        file_cx = stats.get(fid, {}).get("complexity", 0) or 0
        if file_cx > 20:
            score -= 1.5
        elif file_cx > 10:
            score -= 1.0
        elif file_cx > 5:
            score -= 0.5

        # Factor 3: Cycle membership (-1.5 points)
        if fid in cycle_files:
            score -= 1.5

        # Factor 4: God component (-1.0 points)
        if fid in god_files:
            score -= 1.0

        # Factor 5: Dead export ratio (0 to -1.0 points)
        dead_ratio = dead_by_file.get(fid, 0)
        if dead_ratio > 0.5:
            score -= 1.0
        elif dead_ratio > 0.2:
            score -= 0.5

        # Factor 6: Co-change entropy (0 to -1.0 points)
        entropy = stats.get(fid, {}).get("entropy")
        if entropy is not None and entropy > 0.85:
            score -= 1.0
        elif entropy is not None and entropy > 0.7:
            score -= 0.5

        # Clamp to [1, 10]
        score = max(1.0, min(10.0, score))

        # Factor 7: Churn amplification — low health + high churn = worse
        churn = stats.get(fid, {}).get("churn", 0)
        if churn > churn_p90 and score < 6:
            score = max(1.0, score - 1.0)
        elif churn > churn_p50 and score < 5:
            score = max(1.0, score - 0.5)

        score = round(max(1.0, min(10.0, score)), 1)
        updates.append((score, fid))

    with conn:
        conn.executemany(
            "INSERT INTO file_stats (health_score, file_id) VALUES (?, ?) "
            "ON CONFLICT(file_id) DO UPDATE SET health_score = excluded.health_score",
            updates,
        )

    _log(f"  Health scores for {len(updates)} files")


def _compute_cognitive_load(conn):
    """Compute a 0-100 cognitive load index per file.

    Combines five signals into a single 'how hard is this file to
    understand' metric.  Higher = harder to grok.

    Factors:
      1. Max cognitive complexity (brain method)  — 30%
      2. Avg nesting depth across symbols         — 15%
      3. Dependency surface (fan-in + fan-out)     — 20%
      4. Co-change entropy                         — 15%
      5. Dead export ratio                         — 10%
      6. File size (line count)                    — 10%
    """
    files = conn.execute("SELECT id, line_count FROM files").fetchall()
    if not files:
        return

    # 1. Max CC per file
    max_cc = {}
    for r in conn.execute(
        "SELECT s.file_id, MAX(sm.cognitive_complexity) as m "
        "FROM symbol_metrics sm JOIN symbols s ON s.id = sm.symbol_id "
        "GROUP BY s.file_id"
    ).fetchall():
        max_cc[r["file_id"]] = r["m"] or 0

    # 2. Avg nesting depth per file
    avg_nest = {}
    for r in conn.execute(
        "SELECT s.file_id, AVG(sm.nesting_depth) as a "
        "FROM symbol_metrics sm JOIN symbols s ON s.id = sm.symbol_id "
        "GROUP BY s.file_id"
    ).fetchall():
        avg_nest[r["file_id"]] = r["a"] or 0

    # 3. Dependency surface per file (sum of in_degree + out_degree for symbols)
    dep_surface = {}
    for r in conn.execute(
        "SELECT s.file_id, SUM(gm.in_degree + gm.out_degree) as total "
        "FROM graph_metrics gm JOIN symbols s ON s.id = gm.symbol_id "
        "GROUP BY s.file_id"
    ).fetchall():
        dep_surface[r["file_id"]] = r["total"] or 0

    # 4. Co-change entropy (already in file_stats)
    entropy = {}
    for r in conn.execute(
        "SELECT file_id, cochange_entropy FROM file_stats "
        "WHERE cochange_entropy IS NOT NULL"
    ).fetchall():
        entropy[r["file_id"]] = r["cochange_entropy"] or 0

    # 5. Dead export ratio per file
    dead_ratio = {}
    for r in conn.execute(
        "SELECT s.file_id, "
        "COUNT(*) as total, "
        "SUM(CASE WHEN gm.in_degree = 0 THEN 1 ELSE 0 END) as dead "
        "FROM symbols s "
        "LEFT JOIN graph_metrics gm ON gm.symbol_id = s.id "
        "WHERE s.is_exported = 1 "
        "GROUP BY s.file_id"
    ).fetchall():
        total = r["total"] or 1
        dead_ratio[r["file_id"]] = (r["dead"] or 0) / total

    updates = []
    for f in files:
        fid = f["id"]
        lc = f["line_count"] or 0

        cc_norm = min((max_cc.get(fid, 0)) / 50, 1.0)           # 50+ = max
        nest_norm = min((avg_nest.get(fid, 0)) / 6, 1.0)        # 6+ = max
        dep_norm = min((dep_surface.get(fid, 0)) / 40, 1.0)     # 40+ = max
        ent_norm = min(entropy.get(fid, 0), 1.0)                 # already 0-1
        dead_norm = min(dead_ratio.get(fid, 0), 1.0)             # already 0-1
        size_norm = min(lc / 500, 1.0)                           # 500+ = max

        score = (
            cc_norm * 0.30
            + nest_norm * 0.15
            + dep_norm * 0.20
            + ent_norm * 0.15
            + dead_norm * 0.10
            + size_norm * 0.10
        )
        load = round(score * 100, 1)
        updates.append((load, fid))

    with conn:
        conn.executemany(
            "INSERT INTO file_stats (cognitive_load, file_id) VALUES (?, ?) "
            "ON CONFLICT(file_id) DO UPDATE SET cognitive_load = excluded.cognitive_load",
            updates,
        )

    _log(f"  Cognitive load for {len(updates)} files")


def _store_symbols(conn, file_id, rel_path, symbols, all_symbol_rows):
    """Insert extracted symbols into the DB and populate all_symbol_rows."""
    for sym in symbols:
        parent_id = None
        if sym["parent_name"]:
            parent_row = conn.execute(
                "SELECT id FROM symbols WHERE file_id = ? AND name = ?",
                (file_id, sym["parent_name"]),
            ).fetchone()
            if parent_row:
                parent_id = parent_row["id"]

        conn.execute(
            """INSERT INTO symbols
               (file_id, name, qualified_name, kind, signature,
                line_start, line_end, docstring, visibility,
                is_exported, parent_id, default_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_id, sym["name"], sym["qualified_name"],
                sym["kind"], sym["signature"],
                sym["line_start"], sym["line_end"],
                sym["docstring"], sym["visibility"],
                1 if sym["is_exported"] else 0, parent_id,
                sym.get("default_value"),
            ),
        )
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        if not row:
            continue
        sym_id = row[0]
        all_symbol_rows[sym_id] = {
            "id": sym_id,
            "file_id": file_id,
            "file_path": rel_path,
            "name": sym["name"],
            "qualified_name": sym["qualified_name"],
            "kind": sym["kind"],
            "is_exported": bool(sym.get("is_exported")),
            "line_start": sym["line_start"],
        }


def _relink_annotations(conn):
    """Re-link annotations to current symbol IDs via qualified_name.

    After reindex, symbol IDs change.  This function updates the
    ``symbol_id`` column of annotations that have a ``qualified_name``
    recorded, matching them against the new symbols table.
    """
    try:
        conn.execute(
            "UPDATE annotations SET symbol_id = ("
            "  SELECT s.id FROM symbols s "
            "  WHERE s.qualified_name = annotations.qualified_name "
            "  LIMIT 1"
            ") WHERE qualified_name IS NOT NULL"
        )
    except Exception:
        pass  # Table may not exist yet


class Indexer:
    """Orchestrates the full indexing pipeline."""

    def __init__(self, project_root: Path | None = None):
        if project_root is None:
            project_root = find_project_root()
        self.root = Path(project_root).resolve()
        self._quiet = False
        self._progress_bar = True
        self.summary: dict | None = None

    def _log(self, msg: str):
        """Log a message to stderr, respecting quiet mode."""
        if not self._quiet:
            print(msg, file=sys.stderr, flush=True)

    def run(self, force: bool = False, verbose: bool = False,
            include_excluded: bool = False, quiet: bool = False,
            progress_bar: bool = True):
        """Run the indexing pipeline.

        Args:
            force: If True, re-index all files. Otherwise, only changed files.
            verbose: If True, show detailed warnings during indexing.
            include_excluded: If True, skip .roamignore / config / built-in
                exclusion filtering.
            quiet: If True, suppress all progress output to stderr.
            progress_bar: If True (default), show progress bars for file
                processing. Set to False in non-TTY environments.
        """
        global _quiet_mode
        self._quiet = quiet
        self._progress_bar = progress_bar
        _quiet_mode = quiet
        self._log(f"Indexing {self.root}")

        # Lock file to prevent concurrent indexing
        lock_path = self.root / ".roam" / "index.lock"
        lock_path.parent.mkdir(exist_ok=True)
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text().strip())
                try:
                    os.kill(pid, 0)
                    self._log(f"Another indexing process (PID {pid}) is running. Exiting.")
                    return
                except (OSError, SystemError):
                    # OSError: process not found (Unix/Windows)
                    # SystemError: Windows os.kill edge case
                    self._log(f"Removing stale lock file (PID {pid} is not running).")
                    lock_path.unlink()
            except (ValueError, OSError):
                lock_path.unlink()

        lock_path.write_text(str(os.getpid()))
        try:
            self._do_run(force, verbose=verbose,
                         include_excluded=include_excluded)
        finally:
            _quiet_mode = False
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _extract_file_refs(self, rel_path, full_path, language, source,
                           symbols, tree, parsed_source, extractor,
                           all_references, verbose):
        """Extract references from a single file (calls, imports, inheritance)."""
        refs = extract_references(tree, parsed_source, rel_path, extractor)
        for ref in refs:
            ref["source_file"] = rel_path
        all_references.extend(refs)

        # Vue template scanning
        if rel_path.endswith(".vue"):
            tpl_result = extract_vue_template(source if isinstance(source, bytes) else b"")
            if tpl_result:
                tpl_content, tpl_start_line = tpl_result
                known_names = {s["name"] for s in symbols} if symbols else set()
                tpl_refs = scan_template_references(
                    tpl_content, tpl_start_line, known_names, rel_path,
                )
                all_references.extend(tpl_refs)

        # Generic supplement: inheritance refs Tier 1 extractors may miss
        if not isinstance(extractor, GenericExtractor) and language and tree is not None:
            try:
                generic = GenericExtractor(language=language)
                generic_refs = generic.extract_references(tree, parsed_source, rel_path)
                for ref in generic_refs:
                    if ref.get("kind") in ("inherits", "implements", "uses_trait"):
                        ref["source_file"] = rel_path
                        all_references.append(ref)
            except Exception as e:
                if verbose:
                    self._log(f"  Warning: generic extractor failed for {rel_path}: {e}")

    def _process_files(self, conn, files_to_process, get_extractor,
                       compute_complexity_fn, verbose):
        """Parse, extract symbols, and store per-file data. Returns (all_symbol_rows, all_references, file_id_by_path)."""
        all_symbol_rows = {}
        all_references = []
        file_id_by_path = {}
        total = len(files_to_process)

        # Use progress bar when available and not quiet
        use_bar = self._progress_bar and not self._quiet and total > 0
        bar_ctx = None
        bar_obj = None
        if use_bar:
            try:
                import click
                bar_ctx = click.progressbar(
                    length=total,
                    label="Processing",
                    file=sys.stderr,
                    width=36,
                )
                bar_obj = bar_ctx.__enter__()
            except Exception:
                use_bar = False

        try:
            for i, rel_path in enumerate(files_to_process, 1):
                full_path = self.root / rel_path
                language = detect_language(rel_path)

                if use_bar and bar_obj is not None:
                    bar_obj.update(1)
                elif (i % 100 == 0) or (i == total):
                    self._log(f"  Processing {i}/{total} files...")

                if (i % 100 == 0) or (i == total):
                    conn.commit()

                try:
                    with open(full_path, "rb") as f:
                        source = f.read()
                except OSError as e:
                    if verbose:
                        self._log(f"  Warning: Could not read {rel_path}: {e}")
                    continue

                line_count = _count_lines(source)
                complexity = _compute_complexity(source)
                try:
                    mtime = full_path.stat().st_mtime
                except OSError:
                    mtime = None
                fhash = file_hash(full_path)

                content_head = source[:2048].decode("utf-8", errors="replace") if source else None
                file_role = classify_file(rel_path, content_head)

                conn.execute(
                    "INSERT INTO files (path, language, file_role, hash, mtime, line_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (rel_path, language, file_role, fhash, mtime, line_count),
                )
                row = conn.execute("SELECT last_insert_rowid()").fetchone()
                if not row:
                    self._log(f"  Warning: Failed to insert file record for {rel_path}")
                    continue
                file_id = row[0]
                file_id_by_path[rel_path] = file_id

                conn.execute(
                    "INSERT OR REPLACE INTO file_stats (file_id, complexity) VALUES (?, ?)",
                    (file_id, complexity),
                )

                tree, parsed_source, lang = parse_file(full_path, language)
                if tree is None and parsed_source is None:
                    continue

                extractor = None
                if get_extractor is not None and lang is not None:
                    try:
                        extractor = get_extractor(lang)
                    except Exception as e:
                        if verbose:
                            self._log(f"  Warning: No extractor for {lang}: {e}")
                if extractor is None:
                    continue

                symbols = extract_symbols(tree, parsed_source, rel_path, extractor)
                _store_symbols(conn, file_id, rel_path, symbols, all_symbol_rows)

                if compute_complexity_fn is not None and tree is not None:
                    try:
                        compute_complexity_fn(conn, file_id, tree, parsed_source)
                    except Exception as e:
                        if verbose:
                            self._log(f"  Warning: complexity analysis failed for {rel_path}: {e}")

                self._extract_file_refs(
                    rel_path, full_path, language, source, symbols,
                    tree, parsed_source, extractor, all_references, verbose,
                )
        finally:
            if bar_ctx is not None:
                try:
                    bar_ctx.__exit__(None, None, None)
                except Exception:
                    pass

        return all_symbol_rows, all_references, file_id_by_path

    @staticmethod
    def _find_affected_neighbor_files(conn, changed_file_ids):
        """Find file IDs of unchanged files that had edges into changed files.

        When a changed file's symbols are deleted (CASCADE), edges FROM
        other files TO those symbols are also deleted.  We need to re-extract
        references from those "affected neighbor" files to re-establish edges
        pointing to the new symbols.

        Returns a set of file_ids (excluding the changed files themselves).
        """
        if not changed_file_ids:
            return set()

        changed_set = set(changed_file_ids)

        # Use source_file_id if available (v11+ databases)
        has_source_file_id = False
        try:
            row = conn.execute(
                "SELECT source_file_id FROM edges LIMIT 1"
            ).fetchone()
            has_source_file_id = row is not None and row["source_file_id"] is not None
        except Exception:
            pass

        affected = set()
        ph = ",".join("?" for _ in changed_file_ids)

        if has_source_file_id:
            # Fast path: edges already track source_file_id
            rows = conn.execute(
                f"SELECT DISTINCT e.source_file_id "
                f"FROM edges e "
                f"JOIN symbols s_tgt ON e.target_id = s_tgt.id "
                f"WHERE s_tgt.file_id IN ({ph}) "
                f"AND e.source_file_id NOT IN ({ph})",
                changed_file_ids + changed_file_ids,
            ).fetchall()
            affected = {r[0] for r in rows if r[0] is not None}
        else:
            # Fallback for pre-v11 databases: derive source file from source symbol
            rows = conn.execute(
                f"SELECT DISTINCT s_src.file_id "
                f"FROM edges e "
                f"JOIN symbols s_src ON e.source_id = s_src.id "
                f"JOIN symbols s_tgt ON e.target_id = s_tgt.id "
                f"WHERE s_tgt.file_id IN ({ph}) "
                f"AND s_src.file_id NOT IN ({ph})",
                changed_file_ids + changed_file_ids,
            ).fetchall()
            affected = {r[0] for r in rows}

        return affected - changed_set

    def _re_extract_affected(self, conn, affected_file_ids, get_extractor,
                             all_references, verbose):
        """Re-extract references from only the affected neighbor files.

        These are files whose edges into changed files were CASCADE-deleted.
        We surgically delete their remaining edges and re-extract references
        so they can be re-resolved against the updated symbol table.
        """
        if not affected_file_ids:
            return

        # Map file_id -> path for affected files
        ph = ",".join("?" for _ in affected_file_ids)
        fid_list = list(affected_file_ids)
        rows = conn.execute(
            f"SELECT id, path FROM files WHERE id IN ({ph})", fid_list
        ).fetchall()
        affected_paths = {r["id"]: r["path"] for r in rows}

        self._log(f"Re-extracting references from {len(affected_paths)} affected neighbor files...")

        # Delete edges originating from affected files (they'll be rebuilt)
        conn.execute(
            f"DELETE FROM edges WHERE source_file_id IN ({ph})", fid_list
        )
        # Also delete file_edges from affected files
        conn.execute(
            f"DELETE FROM file_edges WHERE source_file_id IN ({ph})", fid_list
        )

        for fid, rel_path in affected_paths.items():
            full_path = self.root / rel_path
            language = detect_language(rel_path)
            tree, parsed_source, lang = parse_file(full_path, language)
            if tree is None and parsed_source is None:
                continue
            extractor = None
            if get_extractor is not None and lang is not None:
                try:
                    extractor = get_extractor(lang)
                except Exception as e:
                    if verbose:
                        self._log(f"  Warning: no extractor for {lang}: {e}")
            if extractor is None:
                continue
            try:
                symbols = extractor.extract_symbols(tree, parsed_source, rel_path)
            except Exception as e:
                symbols = []
                if verbose:
                    self._log(f"  Warning: re-extract symbols failed for {rel_path}: {e}")

            # Read raw source for Vue template scanning
            raw_source = None
            if rel_path.endswith(".vue"):
                from roam.index.parser import read_source
                raw_source = read_source(full_path)

            self._extract_file_refs(
                rel_path, full_path, language, raw_source or b"", symbols,
                tree, parsed_source, extractor, all_references, verbose,
            )

    @staticmethod
    def _backup_annotations(db_path):
        """Read all annotations from the DB before force-reindex deletes it."""
        import gc
        import sqlite3
        if not db_path.exists():
            return []
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            # Check if annotations table exists
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='annotations'"
            ).fetchone()
            if not tables:
                return []
            rows = conn.execute("SELECT * FROM annotations").fetchall()
            result = [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            del conn
            gc.collect()  # Release file handles on Windows

        # Also write to JSON backup for crash safety
        backup_path = db_path.parent / "annotations_backup.json"
        try:
            import json
            backup_path.write_text(
                json.dumps(result, default=str), encoding="utf-8",
            )
        except Exception:
            pass

        return result

    @staticmethod
    def _restore_annotations(conn, saved):
        """Re-insert saved annotations and re-link to new symbol IDs."""
        if not saved:
            return
        for ann in saved:
            conn.execute(
                "INSERT INTO annotations "
                "(qualified_name, file_path, tag, content, author, "
                " created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ann.get("qualified_name"),
                    ann.get("file_path"),
                    ann.get("tag"),
                    ann["content"],
                    ann.get("author"),
                    ann.get("created_at"),
                    ann.get("expires_at"),
                ),
            )
        _relink_annotations(conn)
        _log(f"  Restored {len(saved)} annotations")

    def _do_run(self, force: bool, verbose: bool = False,
                include_excluded: bool = False):
        t0 = time.monotonic()
        self._log("Discovering files...")
        all_files = discover_files(self.root, include_excluded=include_excluded)
        self._log(f"  {_format_count(len(all_files))} files found")

        saved_annotations = []
        if force:
            db_path = get_db_path(self.root)
            if db_path.exists():
                saved_annotations = self._backup_annotations(db_path)
                db_path.unlink()
                for suffix in ("-wal", "-shm"):
                    wal = db_path.parent / (db_path.name + suffix)
                    if wal.exists():
                        wal.unlink()

        with open_db(project_root=self.root) as conn:
            if force:
                added = all_files
                modified = []
                removed = []
            else:
                added, modified, removed = get_changed_files(conn, all_files, self.root)

            total_changed = len(added) + len(modified) + len(removed)
            if total_changed == 0:
                self._log("Index is up to date.")
                self.summary = {"files": 0, "symbols": 0, "edges": 0,
                                "elapsed": 0.0, "up_to_date": True}
                return

            self._log(f"  {len(added)} added, {len(modified)} modified, {len(removed)} removed")

            # Collect file IDs of changed/removed files BEFORE deleting them
            changed_file_ids = []
            for path in removed + modified:
                row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
                if row:
                    changed_file_ids.append(row["id"])

            # Find affected neighbor files BEFORE CASCADE deletes the edges
            # we need for the query.  These are files whose edges INTO the
            # changed files will be lost and need rebuilding.
            affected_file_ids = set()
            if not force and modified and changed_file_ids:
                affected_file_ids = self._find_affected_neighbor_files(
                    conn, changed_file_ids,
                )

            # Now delete the changed/removed file records (CASCADE cleans up
            # their symbols, edges, file_edges, graph_metrics, clusters, etc.)
            for path in removed + modified:
                row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
                if row:
                    fid = row["id"]
                    # Clean up tables with SET NULL FKs (not CASCADE)
                    sym_ids = [r[0] for r in conn.execute(
                        "SELECT id FROM symbols WHERE file_id = ?", (fid,)
                    ).fetchall()]
                    if sym_ids:
                        ph = ",".join("?" for _ in sym_ids)
                        for cleanup_sql in [
                            f"UPDATE runtime_stats SET symbol_id = NULL WHERE symbol_id IN ({ph})",
                            f"UPDATE vulnerabilities SET matched_symbol_id = NULL WHERE matched_symbol_id IN ({ph})",
                        ]:
                            try:
                                conn.execute(cleanup_sql, sym_ids)
                            except Exception:
                                pass  # Table may not exist in older DBs
                    conn.execute("DELETE FROM files WHERE id = ?", (fid,))

            get_extractor = _try_import_get_extractor()
            compute_complexity_fn = _try_import_complexity()

            # 3-6. Parse, extract, store
            files_to_process = added + modified
            all_symbol_rows, all_references, file_id_by_path = self._process_files(
                conn, files_to_process, get_extractor, compute_complexity_fn, verbose,
            )

            # Load existing symbols for incremental
            if not force:
                existing_rows = conn.execute(
                    "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, "
                    "s.is_exported, s.line_start, f.path as file_path "
                    "FROM symbols s JOIN files f ON s.file_id = f.id"
                ).fetchall()
                for row in existing_rows:
                    sid = row["id"]
                    if sid not in all_symbol_rows:
                        all_symbol_rows[sid] = {
                            "id": sid,
                            "file_id": row["file_id"],
                            "file_path": row["file_path"],
                            "name": row["name"],
                            "qualified_name": row["qualified_name"],
                            "kind": row["kind"],
                            "is_exported": bool(row["is_exported"]),
                            "line_start": row["line_start"],
                        }

            for row in conn.execute("SELECT id, path FROM files").fetchall():
                file_id_by_path[row["path"]] = row["id"]

            # Fix incremental edge loss: re-extract only affected neighbors
            # instead of all unchanged files (O(affected) vs O(N))
            if not force and modified and affected_file_ids:
                self._re_extract_affected(
                    conn, affected_file_ids, get_extractor,
                    all_references, verbose,
                )

            # Resolve references into edges
            self._log("Resolving references...")
            symbols_by_name: dict[str, list[dict]] = {}
            for sym in all_symbol_rows.values():
                symbols_by_name.setdefault(sym["name"], []).append(sym)

            symbol_edges = resolve_references(all_references, symbols_by_name, file_id_by_path)

            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind, line, source_file_id) VALUES (?, ?, ?, ?, ?)",
                [(e["source_id"], e["target_id"], e["kind"], e["line"], e.get("source_file_id")) for e in symbol_edges],
            )
            self._log(f"  {_format_count(len(symbol_edges))} symbol edges")

            # Build file edges
            self._log("Building file-level edges...")
            file_edges = build_file_edges(symbol_edges, all_symbol_rows)
            conn.executemany(
                "INSERT INTO file_edges (source_file_id, target_file_id, kind, symbol_count) "
                "VALUES (?, ?, ?, ?)",
                [(fe["source_file_id"], fe["target_file_id"], fe["kind"], fe["symbol_count"]) for fe in file_edges],
            )
            self._log(f"  {_format_count(len(file_edges))} file edges")

            # Graph metrics
            build_symbol_graph, _store_metrics, _detect_clusters, _label_clusters, _store_clusters = _try_import_graph()
            G = None
            if build_symbol_graph is not None:
                self._log("Computing graph metrics...")
                try:
                    G = build_symbol_graph(conn)
                    _store_metrics(conn, G)
                    metric_count = conn.execute("SELECT COUNT(*) FROM graph_metrics").fetchone()[0]
                    self._log(f"  Metrics for {_format_count(metric_count)} symbols")
                except Exception as e:
                    self._log(f"  Graph metrics failed: {e}")
            else:
                self._log("Skipping graph metrics (module not available)")

            # Git history
            analyze_git = _try_import_git_stats()
            if analyze_git is not None:
                self._log("Analyzing git history...")
                try:
                    analyze_git(conn, self.root)
                except Exception as e:
                    self._log(f"  Git analysis failed: {e}")
            else:
                self._log("Skipping git analysis (module not available)")

            # Clusters
            if _detect_clusters is not None and G is not None:
                self._log("Computing clusters...")
                try:
                    cluster_map = _detect_clusters(G)
                    labels = _label_clusters(cluster_map, conn)
                    _store_clusters(conn, cluster_map, labels)
                    self._log(f"  {len(set(cluster_map.values()))} clusters")
                except Exception as e:
                    self._log(f"  Clustering failed: {e}")
            else:
                self._log("Skipping clustering (module not available)")

            # Effect classification + propagation
            _effects_fn = _try_import_effects()
            if _effects_fn is not None:
                self._log("Classifying symbol effects...")
                try:
                    _effects_fn(conn, self.root, G)
                    effect_count = conn.execute(
                        "SELECT COUNT(*) FROM symbol_effects"
                    ).fetchone()[0]
                    if effect_count:
                        self._log(f"  {_format_count(effect_count)} effects classified")
                except Exception as e:
                    self._log(f"  Effect analysis failed: {e}")

            # Per-file health scores — pass G so cycle detection uses SCC, not SQL self-join
            self._log("Computing health scores...")
            try:
                _compute_file_health_scores(conn, G)
            except Exception as e:
                self._log(f"  Health score computation failed: {e}")

            # Cognitive load index
            self._log("Computing cognitive load...")
            try:
                _compute_cognitive_load(conn)
            except Exception as e:
                self._log(f"  Cognitive load computation failed: {e}")

            # Annotation survival
            if force and saved_annotations:
                self._log("Restoring annotations...")
                try:
                    self._restore_annotations(conn, saved_annotations)
                except Exception as e:
                    self._log(f"  Annotation restore failed: {e}")
            elif not force:
                # Re-link annotations after incremental reindex
                try:
                    _relink_annotations(conn)
                except Exception:
                    pass

            # Full-text search index (FTS5/BM25 primary, TF-IDF fallback)
            self._log("Building search index...")
            try:
                from roam.search.index_embeddings import build_fts_index, fts5_available
                build_fts_index(conn, project_root=self.root)
                if fts5_available(conn):
                    fts_count = conn.execute(
                        "SELECT COUNT(*) FROM symbol_fts"
                    ).fetchone()[0]
                    self._log(f"  FTS5 index for {_format_count(fts_count)} symbols")
                else:
                    tfidf_count = conn.execute(
                        "SELECT COUNT(*) FROM symbol_tfidf"
                    ).fetchone()[0]
                    self._log(f"  TF-IDF vectors for {_format_count(tfidf_count)} symbols (FTS5 unavailable)")
                try:
                    onnx_count = conn.execute(
                        "SELECT COUNT(*) FROM symbol_embeddings WHERE provider='onnx'"
                    ).fetchone()[0]
                    if onnx_count:
                        self._log(f"  ONNX vectors for {_format_count(onnx_count)} symbols")
                except Exception:
                    pass
            except Exception as e:
                self._log(f"  Search index build failed (non-fatal): {e}")

            from roam.index.parser import get_parse_error_summary
            error_summary = get_parse_error_summary()
            if error_summary:
                self._log(f"  Parse issues: {error_summary}")

            elapsed = time.monotonic() - t0
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            self._log(
                f"Index complete: {_format_count(file_count)} files, "
                f"{_format_count(sym_count)} symbols, "
                f"{_format_count(edge_count)} edges ({elapsed:.1f}s)"
            )
            self.summary = {
                "files": file_count,
                "symbols": sym_count,
                "edges": edge_count,
                "elapsed": round(elapsed, 1),
                "up_to_date": False,
            }
