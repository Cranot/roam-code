"""Orchestrates the full indexing pipeline."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from roam.db.connection import find_project_root, get_db_path, open_db
from roam.index.discovery import discover_files
from roam.index.file_roles import classify_file
from roam.index.incremental import file_hash, get_changed_files
from roam.index.parser import (
    detect_language,
    extract_vue_template,
    parse_file,
    scan_template_references,
)
from roam.index.relations import build_file_edges, resolve_references
from roam.index.symbols import extract_references, extract_symbols
from roam.languages.generic_lang import GenericExtractor


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
        from roam.graph.clusters import detect_clusters, label_clusters, store_clusters
        from roam.graph.pagerank import store_metrics

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


def _try_import_taint():
    """Try to import the taint analysis module."""
    try:
        from roam.analysis.taint import compute_and_store_taint

        return compute_and_store_taint
    except ImportError:
        return None


_quiet_mode = False


def _log(msg: str):
    if not _quiet_mode:
        sys.stderr.write(f"{msg}\n")
        sys.stderr.flush()


def _pid_is_running(pid: int) -> bool:
    """Return True when *pid* appears to refer to a live process.

    On Windows, probing a process owned by another integrity level can raise
    ``PermissionError``. Treat that as "running or inaccessible" so we do not
    delete another process's lock by mistake.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def _claim_index_lock(lock_path: Path) -> bool:
    """Claim the index lock, tolerating stale locks that cannot be deleted."""
    lock_path.parent.mkdir(exist_ok=True)
    if lock_path.exists():
        try:
            raw_pid = lock_path.read_text().strip()
            pid = int(raw_pid)
        except (ValueError, OSError):
            pid = 0

        if pid and _pid_is_running(pid):
            _log(f"Another indexing process (PID {pid}) is running. Exiting.")
            return False

        if pid:
            _log(f"Removing stale lock file (PID {pid} is not running).")
        try:
            lock_path.unlink()
        except OSError as exc:
            _log(f"  Could not delete stale lock ({exc}); reusing lock file.")

    try:
        lock_path.write_text(str(os.getpid()))
    except OSError as exc:
        _log(f"Could not claim index lock: {exc}")
        _log("  If this looks unexpected, run `roam doctor` to diagnose your install.")
        return False
    return True


def _release_index_lock(lock_path: Path) -> None:
    """Release the index lock best-effort.

    Some Windows/cloud-sync folders allow overwrites but deny deletes. Marking
    the lock as released lets the next run overwrite it without failing.
    """
    try:
        lock_path.unlink()
        return
    except OSError:
        pass
    try:
        lock_path.write_text("released")
    except OSError:
        pass


def _semantic_activation_advice(conn, project_root: Path) -> str | None:
    """Return an index-time hint when semantic retrieve weighting is inert."""
    try:
        from roam.config import get_retrieve_config
        from roam.retrieve.semantic import semantic_coverage

        zeta = float(get_retrieve_config(project_root).get("zeta", 0.0) or 0.0)
        coverage = semantic_coverage(conn)
    except Exception:
        return None
    if zeta <= 0 or coverage["ready"] or int(coverage["symbols"]) <= 0:
        return None
    return (
        f"Semantic retrieve: {coverage['embeddings']}/{coverage['symbols']} dense vectors; "
        f"zeta={zeta:g} is inert until semantic backend vectors are populated."
    )


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
    stat_rows = conn.execute("SELECT file_id, total_churn, complexity, cochange_entropy FROM file_stats").fetchall()
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
        "SELECT file_id, cochange_entropy FROM file_stats WHERE cochange_entropy IS NOT NULL"
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

        cc_norm = min((max_cc.get(fid, 0)) / 50, 1.0)  # 50+ = max
        nest_norm = min((avg_nest.get(fid, 0)) / 6, 1.0)  # 6+ = max
        dep_norm = min((dep_surface.get(fid, 0)) / 40, 1.0)  # 40+ = max
        ent_norm = min(entropy.get(fid, 0), 1.0)  # already 0-1
        dead_norm = min(dead_ratio.get(fid, 0), 1.0)  # already 0-1
        size_norm = min(lc / 500, 1.0)  # 500+ = max

        score = (
            cc_norm * 0.30 + nest_norm * 0.15 + dep_norm * 0.20 + ent_norm * 0.15 + dead_norm * 0.10 + size_norm * 0.10
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
                is_exported, parent_id, default_value,
                is_async, decorators)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_id,
                sym["name"],
                sym["qualified_name"],
                sym["kind"],
                sym["signature"],
                sym["line_start"],
                sym["line_end"],
                sym["docstring"],
                sym["visibility"],
                1 if sym["is_exported"] else 0,
                parent_id,
                sym.get("default_value"),
                1 if sym.get("is_async") else 0,
                sym.get("decorators") or "",
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
            sys.stderr.write(f"{msg}\n")
            sys.stderr.flush()

    def run(
        self,
        force: bool = False,
        verbose: bool = False,
        include_excluded: bool = False,
        quiet: bool = False,
        progress_bar: bool = True,
    ):
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
        if not _claim_index_lock(lock_path):
            return
        try:
            self._do_run(force, verbose=verbose, include_excluded=include_excluded)
        except KeyboardInterrupt:
            # graceful Ctrl-C: drop the lock so the user can
            # rerun without manual cleanup. Periodic commits in
            # _advance_processing_progress mean we keep what's been
            # processed; the indexer is incremental and resumes safely.
            self._log("Indexing interrupted. Lock released; rerun `roam index` to resume.")
            raise
        finally:
            _quiet_mode = False
            _release_index_lock(lock_path)

    def _extract_file_refs(
        self,
        rel_path,
        full_path,
        language,
        source,
        symbols,
        tree,
        parsed_source,
        extractor,
        all_references,
        verbose,
    ):
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
                    tpl_content,
                    tpl_start_line,
                    known_names,
                    rel_path,
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

    def _start_processing_progress(self, total):
        if not self._progress_bar or self._quiet or total <= 0:
            return None, None
        try:
            import click

            bar_ctx = click.progressbar(
                length=total,
                label="Processing",
                file=sys.stderr,
                width=36,
            )
            return bar_ctx, bar_ctx.__enter__()
        except Exception:
            return None, None

    def _advance_processing_progress(self, conn, bar_obj, index: int, total: int) -> None:
        if bar_obj is not None:
            bar_obj.update(1)
        elif (index % 100 == 0) or (index == total):
            self._log(f"  Processing {index}/{total} files...")

        if (index % 100 == 0) or (index == total):
            conn.commit()

    @staticmethod
    def _close_processing_progress(bar_ctx) -> None:
        if bar_ctx is None:
            return
        try:
            bar_ctx.__exit__(None, None, None)
        except Exception:
            pass

    def _read_index_source(self, full_path: Path, rel_path: str, verbose: bool) -> bytes | None:
        # Prefetched cache hit (parallel I/O prefetch).
        cache = getattr(self, "_source_cache", None)
        if cache is not None and rel_path in cache:
            return cache.pop(rel_path)
        try:
            with open(full_path, "rb") as f:
                return f.read()
        except OSError as e:
            if verbose:
                self._log(f"  Warning: Could not read {rel_path}: {e}")
            return None

    def _prefetch_sources(self, files_to_process, verbose: bool) -> None:
        """Pre-read source bytes into memory in parallel.

        opt-in via ``ROAM_PARALLEL_INDEX=1``. File I/O dominates
        on cold caches (network drives, OneDrive, etc.). A thread pool
        eliminates that wait without touching the (serial) DB write path.
        Bytes are stored on ``self._source_cache`` and consumed by
        ``_read_index_source`` so the rest of the pipeline is unchanged.
        """
        if os.environ.get("ROAM_PARALLEL_INDEX", "").strip() not in {"1", "true", "yes"}:
            return
        from concurrent.futures import ThreadPoolExecutor

        def _read(rel_path: str) -> tuple[str, bytes | None]:
            full_path = self.root / rel_path
            try:
                with open(full_path, "rb") as f:
                    return rel_path, f.read()
            except OSError:
                return rel_path, None

        max_workers = min(32, (os.cpu_count() or 4) * 2)
        cache: dict[str, bytes] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for rel_path, data in ex.map(_read, list(files_to_process)):
                if data is not None:
                    cache[rel_path] = data
        self._source_cache = cache
        if verbose:
            self._log(f"  Prefetched {len(cache)} source files in parallel")

    def _insert_file_record(
        self, conn, full_path: Path, rel_path: str, language: str | None, source: bytes
    ) -> int | None:
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
            return None

        file_id = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO file_stats (file_id, complexity) VALUES (?, ?)",
            (file_id, complexity),
        )
        return file_id

    def _extractor_for_file(self, get_extractor, lang, rel_path: str, verbose: bool):
        if get_extractor is None or lang is None:
            return None
        try:
            return get_extractor(lang)
        except Exception as e:
            if verbose:
                self._log(f"  Warning: No extractor for {lang}: {e}")
            return None

    def _compute_symbol_metrics(
        self, conn, file_id, tree, parsed_source, rel_path: str, compute_complexity_fn, verbose: bool
    ) -> None:
        if compute_complexity_fn is None or tree is None:
            return
        try:
            compute_complexity_fn(conn, file_id, tree, parsed_source)
        except Exception as e:
            if verbose:
                self._log(f"  Warning: complexity analysis failed for {rel_path}: {e}")

    def _process_single_file(
        self,
        conn,
        rel_path,
        get_extractor,
        compute_complexity_fn,
        all_symbol_rows,
        all_references,
        verbose,
    ) -> int | None:
        full_path = self.root / rel_path
        language = detect_language(rel_path)
        source = self._read_index_source(full_path, rel_path, verbose)
        if source is None:
            return None

        file_id = self._insert_file_record(conn, full_path, rel_path, language, source)
        if file_id is None:
            return None

        tree, parsed_source, lang = parse_file(full_path, language)
        if tree is None and parsed_source is None:
            return file_id

        extractor = self._extractor_for_file(get_extractor, lang, rel_path, verbose)
        if extractor is None:
            return file_id

        symbols = extract_symbols(tree, parsed_source, rel_path, extractor)
        _store_symbols(conn, file_id, rel_path, symbols, all_symbol_rows)
        self._compute_symbol_metrics(conn, file_id, tree, parsed_source, rel_path, compute_complexity_fn, verbose)
        self._extract_file_refs(
            rel_path,
            full_path,
            language,
            source,
            symbols,
            tree,
            parsed_source,
            extractor,
            all_references,
            verbose,
        )
        return file_id

    def _process_files(self, conn, files_to_process, get_extractor, compute_complexity_fn, verbose):
        """Parse, extract symbols, and store per-file data. Returns (all_symbol_rows, all_references, file_id_by_path)."""
        all_symbol_rows = {}
        all_references = []
        file_id_by_path = {}
        total = len(files_to_process)
        self._prefetch_sources(files_to_process, verbose)
        bar_ctx, bar_obj = self._start_processing_progress(total)

        try:
            for i, rel_path in enumerate(files_to_process, 1):
                self._advance_processing_progress(conn, bar_obj, i, total)
                file_id = self._process_single_file(
                    conn,
                    rel_path,
                    get_extractor,
                    compute_complexity_fn,
                    all_symbol_rows,
                    all_references,
                    verbose,
                )
                if file_id is not None:
                    file_id_by_path[rel_path] = file_id
        finally:
            self._close_processing_progress(bar_ctx)

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
            row = conn.execute("SELECT source_file_id FROM edges LIMIT 1").fetchone()
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

    def _re_extract_affected(self, conn, affected_file_ids, get_extractor, all_references, verbose):
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
        rows = conn.execute(f"SELECT id, path FROM files WHERE id IN ({ph})", fid_list).fetchall()
        affected_paths = {r["id"]: r["path"] for r in rows}

        self._log(f"Re-extracting references from {len(affected_paths)} affected neighbor files...")

        # Delete edges originating from affected files (they'll be rebuilt)
        conn.execute(f"DELETE FROM edges WHERE source_file_id IN ({ph})", fid_list)
        # Also delete file_edges from affected files
        conn.execute(f"DELETE FROM file_edges WHERE source_file_id IN ({ph})", fid_list)

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
                rel_path,
                full_path,
                language,
                raw_source or b"",
                symbols,
                tree,
                parsed_source,
                extractor,
                all_references,
                verbose,
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
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='annotations'").fetchone()
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
                json.dumps(result, default=str),
                encoding="utf-8",
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

    def _reset_index_for_force(self, force: bool) -> list[dict]:
        if not force:
            return []
        db_path = get_db_path(self.root)
        if not db_path.exists():
            return []

        saved_annotations = self._backup_annotations(db_path)
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            wal = db_path.parent / (db_path.name + suffix)
            if wal.exists():
                wal.unlink()
        return saved_annotations

    @staticmethod
    def _file_ids_for_paths(conn, paths: list[str]) -> list[int]:
        changed_file_ids = []
        for path in paths:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
            if row:
                changed_file_ids.append(row["id"])
        return changed_file_ids

    @staticmethod
    def _clear_nullable_symbol_refs(conn, file_id: int) -> None:
        sym_ids = [r[0] for r in conn.execute("SELECT id FROM symbols WHERE file_id = ?", (file_id,)).fetchall()]
        if not sym_ids:
            return
        ph = ",".join("?" for _ in sym_ids)
        for cleanup_sql in [
            f"UPDATE runtime_stats SET symbol_id = NULL WHERE symbol_id IN ({ph})",
            f"UPDATE vulnerabilities SET matched_symbol_id = NULL WHERE matched_symbol_id IN ({ph})",
        ]:
            try:
                conn.execute(cleanup_sql, sym_ids)
            except Exception:
                pass  # Table may not exist in older DBs

    def _delete_changed_files(self, conn, paths: list[str]) -> None:
        for path in paths:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
            if not row:
                continue
            fid = row["id"]
            self._clear_nullable_symbol_refs(conn, fid)
            conn.execute("DELETE FROM files WHERE id = ?", (fid,))

    @staticmethod
    def _merge_existing_symbols(conn, all_symbol_rows: dict) -> None:
        existing_rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, "
            "s.is_exported, s.line_start, f.path as file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id"
        ).fetchall()
        for row in existing_rows:
            sid = row["id"]
            if sid in all_symbol_rows:
                continue
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

    @staticmethod
    def _refresh_file_id_map(conn, file_id_by_path: dict) -> None:
        for row in conn.execute("SELECT id, path FROM files").fetchall():
            file_id_by_path[row["path"]] = row["id"]

    def _resolve_and_store_edges(self, conn, all_references, all_symbol_rows, file_id_by_path) -> None:
        # Phase header is emitted by ``_do_run`` via ``_begin_phase``; the
        # per-method log only emits result counts now to avoid duplicate
        # "Resolving references..." lines under inline-progress mode.
        symbols_by_name: dict[str, list[dict]] = {}
        for sym in all_symbol_rows.values():
            symbols_by_name.setdefault(sym["name"], []).append(sym)

        symbol_edges = resolve_references(all_references, symbols_by_name, file_id_by_path)
        conn.executemany(
            "INSERT INTO edges (source_id, target_id, kind, line, source_file_id) VALUES (?, ?, ?, ?, ?)",
            [(e["source_id"], e["target_id"], e["kind"], e["line"], e.get("source_file_id")) for e in symbol_edges],
        )
        self._log(f"  {_format_count(len(symbol_edges))} symbol edges")

        self._log("Building file-level edges...")
        file_edges = build_file_edges(symbol_edges, all_symbol_rows)
        conn.executemany(
            "INSERT INTO file_edges (source_file_id, target_file_id, kind, symbol_count) VALUES (?, ?, ?, ?)",
            [(fe["source_file_id"], fe["target_file_id"], fe["kind"], fe["symbol_count"]) for fe in file_edges],
        )
        self._log(f"  {_format_count(len(file_edges))} file edges")

    def _compute_graph_metrics(self, conn):
        (
            build_symbol_graph,
            _store_metrics,
            _detect_clusters,
            _label_clusters,
            _store_clusters,
        ) = _try_import_graph()
        G = None
        if build_symbol_graph is None:
            self._log("Skipping graph metrics (module not available)")
            return G, _detect_clusters, _label_clusters, _store_clusters

        # Phase header emitted by _begin_phase in _do_run.
        try:
            G = build_symbol_graph(conn)
            _store_metrics(conn, G)
            metric_count = conn.execute("SELECT COUNT(*) FROM graph_metrics").fetchone()[0]
            self._log(f"  Metrics for {_format_count(metric_count)} symbols")
        except Exception as e:
            self._log(f"  Graph metrics failed: {e}")
        return G, _detect_clusters, _label_clusters, _store_clusters

    def _run_django_post_resolver(self, conn) -> None:
        try:
            from roam.index.django_post import resolve_all_django

            django_counts = resolve_all_django(conn, quiet=True)
            if any(django_counts.values()):
                parts = []
                if django_counts.get("models_updated"):
                    parts.append(f"{django_counts['models_updated']} model(s)")
                if django_counts.get("fields_updated"):
                    parts.append(f"{django_counts['fields_updated']} field(s)")
                if django_counts.get("relationships_created"):
                    parts.append(f"{django_counts['relationships_created']} relationship edge(s)")
                if parts:
                    self._log(f"  Django: {', '.join(parts)}")
        except Exception as e:
            self._log(f"  Django post-resolver skipped: {e}")

    def _run_pytest_fixture_resolver(self, conn) -> None:
        try:
            from roam.index.pytest_fixtures import resolve_pytest_fixtures

            fixture_edges = resolve_pytest_fixtures(conn)
            if fixture_edges:
                self._log(f"  pytest fixtures: {fixture_edges} dependency edge(s)")
        except Exception as e:
            self._log(f"  pytest fixture resolver skipped: {e}")

    def _run_laravel_post_resolver(self, conn) -> None:
        try:
            from roam.index.laravel_post import resolve_laravel_dispatch

            laravel_edges = resolve_laravel_dispatch(conn, self.root)
            if laravel_edges:
                self._log(f"  Laravel dispatch: {laravel_edges} edge(s)")
        except Exception as e:
            self._log(f"  Laravel post-resolver skipped: {e}")

    def _run_registry_dispatch_resolver(self, conn) -> None:
        try:
            from roam.index.registry_dispatch import resolve_registry_dispatch

            dispatch_edges = resolve_registry_dispatch(conn)
            if dispatch_edges:
                self._log(f"  registry dispatch: {dispatch_edges} edge(s)")
        except Exception as e:
            self._log(f"  registry-dispatch resolver skipped: {e}")

    def _record_step(self, step: str, status: str) -> None:
        """Track which sub-step succeeded / failed / was skipped.

        Audit A8: the indexer runs ~10 sub-steps with try/except
        log-and-continue. Without per-step status, ``roam doctor``
        can't tell "your index is missing taint analysis because
        that step failed" from "everything ran cleanly." The
        step-completion record lets the doctor surface specific
        degraded-mode signals.

        Persisted via ``_record_manifest`` into the manifest's
        ``notes`` field as a JSON blob (no schema change required).
        """
        # Initialise lazily — keeps __init__ untouched.
        if not hasattr(self, "_step_status") or self._step_status is None:
            self._step_status = {}
        self._step_status[step] = status

    def _run_git_analysis(self, conn) -> None:
        analyze_git = _try_import_git_stats()
        if analyze_git is None:
            self._log("Skipping git analysis (module not available)")
            self._record_step("git_analysis", "skipped:module_missing")
            return

        # Phase header emitted by _begin_phase in _do_run.
        try:
            analyze_git(conn, self.root)
            self._record_step("git_analysis", "ok")
        except Exception as e:
            self._log(f"  Git analysis failed: {e}")
            self._record_step("git_analysis", f"failed:{type(e).__name__}")

    @staticmethod
    def _compute_graph_signature(G) -> dict | None:
        """Cheap structural signature of *G* for cluster-cache gating.

        Captures node count, edge count, and the IDs of the top-N highest
        degree nodes (sorted). When all three match the persisted
        signature on the previous run AND the previous cluster table is
        non-empty, the Louvain pass can be skipped — its output is still
        valid for this graph topology.

        We deliberately avoid hashing the full edge list: on 17K
        symbols / 17K edges this method runs in <50ms (top-N partial
        sort) versus ~3-11s for Louvain. The top-N IDs catch most
        meaningful structural changes (any new high-fan symbol reshapes
        community membership).
        """
        if G is None or len(G) == 0:
            return None
        try:
            n = G.number_of_nodes()
            m = G.number_of_edges()
            # Top-N high-degree nodes — N=64 is enough to detect "anything
            # interesting reshuffled" without paying a full sort.
            top_n = 64
            # Use total degree (in + out) so direction-flips also register.
            degs = [(node, G.in_degree(node) + G.out_degree(node)) for node in G.nodes()]
            degs.sort(key=lambda p: (-p[1], p[0]))
            top_ids = sorted(int(p[0]) for p in degs[:top_n])
            return {"n": int(n), "m": int(m), "top": top_ids}
        except Exception:
            return None

    @staticmethod
    def _previous_cluster_signature(conn) -> dict | None:
        """Read the most recent persisted cluster signature, or None.

        Pulled from the ``notes`` JSON of the latest ``index_manifest``
        row under the ``cluster_signature`` key. Tolerant of all the
        shapes the manifest might be in (missing table, no rows, notes
        not JSON, key absent).
        """
        try:
            from roam.index.manifest import latest_manifest

            prev = latest_manifest(conn)
        except Exception:
            return None
        if not prev:
            return None
        notes_raw = prev.get("notes")
        if not notes_raw:
            return None
        try:
            import json as _json

            notes = _json.loads(notes_raw)
        except Exception:
            return None
        sig = notes.get("cluster_signature") if isinstance(notes, dict) else None
        if not isinstance(sig, dict):
            return None
        return sig

    @staticmethod
    def _has_existing_clusters(conn) -> bool:
        """Return True if the clusters table has at least one row."""
        try:
            row = conn.execute("SELECT 1 FROM clusters LIMIT 1").fetchone()
            return row is not None
        except Exception:
            return False

    def _run_clustering(
        self,
        conn,
        G,
        detect_clusters,
        label_clusters,
        store_clusters,
        force: bool = False,
    ) -> None:
        if detect_clusters is None or G is None:
            self._log("Skipping clustering (module not available)")
            self._record_step("clustering", "skipped:module_missing" if detect_clusters is None else "skipped:no_graph")
            return

        # Cache the live graph signature so _record_manifest can persist
        # it whether we ran Louvain or skipped it.
        live_sig = self._compute_graph_signature(G)
        self._cluster_signature = live_sig

        if not force and live_sig is not None:
            prev_sig = self._previous_cluster_signature(conn)
            if (
                prev_sig is not None
                and prev_sig.get("n") == live_sig["n"]
                and prev_sig.get("m") == live_sig["m"]
                and list(prev_sig.get("top") or ()) == live_sig["top"]
                and self._has_existing_clusters(conn)
            ):
                self._log("Computing clusters...")
                self._log(
                    f"  cached: graph signature unchanged "
                    f"({live_sig['n']} nodes, {live_sig['m']} edges) — skipping Louvain"
                )
                self._record_step("clustering", "ok:cached")
                return

        self._log("Computing clusters...")
        try:
            cluster_map = detect_clusters(G)
            labels = label_clusters(cluster_map, conn)
            store_clusters(conn, cluster_map, labels)
            self._log(f"  {len(set(cluster_map.values()))} clusters")
            self._record_step("clustering", "ok")
        except Exception as e:
            self._log(f"  Clustering failed: {e}")
            self._record_step("clustering", f"failed:{type(e).__name__}")

    def _run_effect_analysis(self, conn, G) -> None:
        effects_fn = _try_import_effects()
        if effects_fn is None:
            self._record_step("effect_analysis", "skipped:module_missing")
            return

        # Phase header emitted by _begin_phase in _do_run (effects + taint share phase 5).
        try:
            effects_fn(conn, self.root, G)
            effect_count = conn.execute("SELECT COUNT(*) FROM symbol_effects").fetchone()[0]
            if effect_count:
                self._log(f"  {_format_count(effect_count)} effects classified")
            self._record_step("effect_analysis", "ok")
        except Exception as e:
            self._log(f"  Effect analysis failed: {e}")
            self._record_step("effect_analysis", f"failed:{type(e).__name__}")

    def _run_taint_analysis(self, conn, G) -> None:
        taint_fn = _try_import_taint()
        if taint_fn is None:
            self._record_step("taint_analysis", "skipped:module_missing")
            return

        # Phase header emitted by _begin_phase in _do_run (effects + taint share phase 5).
        try:
            taint_fn(conn, self.root, G)
            taint_count = conn.execute("SELECT COUNT(*) FROM taint_findings").fetchone()[0]
            if taint_count:
                self._log(f"  {_format_count(taint_count)} taint findings")
            self._record_step("taint_analysis", "ok")
        except Exception as e:
            self._log(f"  Taint analysis failed: {e}")
            self._record_step("taint_analysis", f"failed:{type(e).__name__}")

    def _compute_health_and_load(self, conn, G) -> None:
        self._log("Computing health scores...")
        try:
            _compute_file_health_scores(conn, G)
            self._record_step("health_scores", "ok")
        except Exception as e:
            self._log(f"  Health score computation failed: {e}")
            self._record_step("health_scores", f"failed:{type(e).__name__}")

        self._log("Computing cognitive load...")
        try:
            _compute_cognitive_load(conn)
            self._record_step("cognitive_load", "ok")
        except Exception as e:
            self._log(f"  Cognitive load computation failed: {e}")
            self._record_step("cognitive_load", f"failed:{type(e).__name__}")

    def _restore_or_relink_annotations(self, conn, force: bool, saved_annotations: list[dict]) -> None:
        if force and saved_annotations:
            self._log("Restoring annotations...")
            try:
                self._restore_annotations(conn, saved_annotations)
            except Exception as e:
                self._log(f"  Annotation restore failed: {e}")
            return

        if not force:
            try:
                _relink_annotations(conn)
            except Exception:
                pass

    def _build_search_indexes(self, conn, force: bool = False) -> None:
        # Phase header emitted by _begin_phase in _do_run.
        # ``force=True`` (passed when the user ran ``roam index --rebuild``)
        # triggers a full DELETE+INSERT in build_fts_index. Default is
        # incremental: FTS5 is diffed against symbols and only the
        # delta is applied (R9.B7).
        try:
            from roam.search.index_embeddings import build_fts_index, fts5_available

            build_fts_index(conn, project_root=self.root, force=force)
            if fts5_available(conn):
                fts_count = conn.execute("SELECT COUNT(*) FROM symbol_fts").fetchone()[0]
                self._log(f"  FTS5 index for {_format_count(fts_count)} symbols")
            else:
                tfidf_count = conn.execute("SELECT COUNT(*) FROM symbol_tfidf").fetchone()[0]
                self._log(f"  TF-IDF vectors for {_format_count(tfidf_count)} symbols (FTS5 unavailable)")
            try:
                onnx_count = conn.execute("SELECT COUNT(*) FROM symbol_embeddings WHERE provider='onnx'").fetchone()[0]
                if onnx_count:
                    self._log(f"  ONNX vectors for {_format_count(onnx_count)} symbols")
            except Exception:
                pass
            advice = _semantic_activation_advice(conn, self.root)
            if advice:
                self._log(f"  {advice}")
        except Exception as e:
            self._log(f"  Search index build failed (non-fatal): {e}")

    def _log_parse_issues(self) -> None:
        from roam.index.parser import get_parse_error_summary

        error_summary = get_parse_error_summary()
        if error_summary:
            self._log(f"  Parse issues: {error_summary}")

    def _record_manifest(self, conn, *, force: bool, include_excluded: bool) -> None:
        """Persist an index_manifest row capturing this run's environment.

        Best-effort: any failure logs (in verbose mode via ROAM_DEBUG) but
        never aborts indexing. The manifest is consumed by ``roam doctor``
        and bundle-import drift checks; missing it just means those
        consumers fall back to "no manifest".

        Per-step status is persisted as a JSON blob in the manifest's
        ``notes`` field (audit A8) so ``roam doctor`` can surface
        "your index is missing taint analysis because that step failed"
        rather than the generic stale-manifest signal. No schema change
        — the ``notes`` field already exists and is documented as
        free-form.
        """
        try:
            from roam.index.manifest import record_indexer_run
        except Exception:
            return
        # Mix the CLI flags that affect indexing into the config hash so a
        # flag flip invalidates the manifest comparison even when the
        # config files themselves are unchanged.
        flags = [
            f"force={1 if force else 0}",
            f"include_excluded={1 if include_excluded else 0}",
        ]
        notes = None
        step_status = getattr(self, "_step_status", None)
        cluster_sig = getattr(self, "_cluster_signature", None)
        notes_payload: dict = {}
        if step_status:
            notes_payload["steps"] = step_status
        if cluster_sig:
            notes_payload["cluster_signature"] = cluster_sig
        if notes_payload:
            import json as _json

            notes = _json.dumps(notes_payload, sort_keys=True)
        record_indexer_run(
            conn,
            self.root,
            profile="all",
            extra_config_inputs=flags,
            notes=notes,
        )

    def _set_completion_summary(self, conn, elapsed: float) -> None:
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        self._log(
            f"Index complete: {_format_count(file_count)} files, "
            f"{_format_count(sym_count)} symbols, "
            f"{_format_count(edge_count)} edges ({elapsed:.1f}s)"
        )
        self._log("Index complete.")
        self.summary = {
            "files": file_count,
            "symbols": sym_count,
            "edges": edge_count,
            "elapsed": round(elapsed, 1),
            "up_to_date": False,
        }

    # User-facing phase numbering for the inline progress log (G13).
    # Kept in sync with the order of calls in ``_do_run`` below. The
    # number is a hint for the user, not a contract — when phases
    # split or merge, bump _PHASE_COUNT and update the calls below.
    _PHASE_COUNT = 7

    def _begin_phase(self, n: int, label: str) -> None:
        """Emit a ``[n/N] label...`` line before the next pipeline step.

        Skipped under ``--quiet``. Calls go to stderr (the same
        channel as the click progressbar) so they interleave cleanly
        in CI logs and don't pollute stdout JSON output.
        """
        if not self._quiet:
            self._log(f"  [{n}/{self._PHASE_COUNT}] {label}")

    def _do_run(self, force: bool, verbose: bool = False, include_excluded: bool = False):
        t0 = time.monotonic()
        self._log("Discovering files...")
        all_files = discover_files(self.root, include_excluded=include_excluded)
        self._log(f"  {_format_count(len(all_files))} files found")

        saved_annotations = self._reset_index_for_force(force)

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
                self.summary = {
                    "files": 0,
                    "symbols": 0,
                    "edges": 0,
                    "elapsed": 0.0,
                    "up_to_date": True,
                }
                return

            self._log(f"  {len(added)} added, {len(modified)} modified, {len(removed)} removed")

            changed_paths = removed + modified
            changed_file_ids = self._file_ids_for_paths(conn, changed_paths)
            affected_file_ids = set()
            if not force and changed_file_ids:
                # A pure rename (modified=[], removed=[old]) still needs neighbor
                # recovery: edges into old.qualified_name are CASCADE-deleted, and
                # the third-party callers must be re-extracted to point at the new
                # symbol id. Gating on `modified` truthiness skipped that path.
                affected_file_ids = self._find_affected_neighbor_files(conn, changed_file_ids)

            self._delete_changed_files(conn, changed_paths)

            get_extractor = _try_import_get_extractor()
            compute_complexity_fn = _try_import_complexity()

            # 3-6. Parse, extract, store
            files_to_process = added + modified
            self._begin_phase(1, f"Parsing & extracting symbols ({len(files_to_process)} files)...")
            all_symbol_rows, all_references, file_id_by_path = self._process_files(
                conn,
                files_to_process,
                get_extractor,
                compute_complexity_fn,
                verbose,
            )

            # Load existing symbols for incremental
            if not force:
                self._merge_existing_symbols(conn, all_symbol_rows)

            self._refresh_file_id_map(conn, file_id_by_path)

            # Fix incremental edge loss: re-extract only affected neighbors
            # instead of all unchanged files (O(affected) vs O(N)).
            #
            # Drop the historical `and modified` clause: a pure rename
            # (added=[new], removed=[old], modified=[]) still CASCADE-deletes
            # the old symbol rows, which removes edges into them. The neighbor
            # callers must be re-extracted to retarget the new symbol ids, or
            # `roam impact <renamed_symbol>` silently under-reports callers.
            # `affected_file_ids` already encodes "anything to re-extract";
            # `changed_file_ids` is its source and is non-empty for renames too.
            if not force and affected_file_ids:
                self._re_extract_affected(
                    conn,
                    affected_file_ids,
                    get_extractor,
                    all_references,
                    verbose,
                )

            self._begin_phase(2, "Resolving references...")
            self._resolve_and_store_edges(conn, all_references, all_symbol_rows, file_id_by_path)
            self._run_django_post_resolver(conn)
            self._run_pytest_fixture_resolver(conn)
            self._run_registry_dispatch_resolver(conn)
            self._run_laravel_post_resolver(conn)
            self._begin_phase(3, "Computing graph metrics...")
            G, detect_clusters, label_clusters, store_clusters = self._compute_graph_metrics(conn)
            self._begin_phase(4, "Analyzing git history...")
            self._run_git_analysis(conn)
            self._run_clustering(conn, G, detect_clusters, label_clusters, store_clusters, force=force)
            self._begin_phase(5, "Computing effects & taint flow...")
            self._run_effect_analysis(conn, G)
            self._run_taint_analysis(conn, G)
            self._begin_phase(6, "Computing health & cognitive load...")
            self._compute_health_and_load(conn, G)
            self._restore_or_relink_annotations(conn, force, saved_annotations)
            self._begin_phase(7, "Building search indexes...")
            self._build_search_indexes(conn, force=force)
            self._log_parse_issues()
            self._set_completion_summary(conn, time.monotonic() - t0)
            self._record_manifest(conn, force=force, include_excluded=include_excluded)

        try:
            from roam.graph.builder import clear_graph_cache

            clear_graph_cache()
        except Exception:
            pass
