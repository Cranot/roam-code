"""Orchestrates the full indexing pipeline."""

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


def _try_import_git_stats():
    """Try to import git stats module."""
    try:
        from roam.index.git_stats import collect_git_stats
        return collect_git_stats
    except ImportError:
        return None


def _log(msg: str):
    print(msg, file=sys.stderr)


class Indexer:
    """Orchestrates the full indexing pipeline."""

    def __init__(self, project_root: Path | None = None):
        if project_root is None:
            project_root = find_project_root()
        self.root = Path(project_root).resolve()

    def run(self, force: bool = False, verbose: bool = False):
        """Run the indexing pipeline.

        Args:
            force: If True, re-index all files. Otherwise, only changed files.
            verbose: If True, show detailed warnings during indexing.
        """
        _log(f"Indexing {self.root}")

        # Lock file to prevent concurrent indexing
        lock_path = self.root / ".roam" / "index.lock"
        lock_path.parent.mkdir(exist_ok=True)
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text().strip())
                try:
                    os.kill(pid, 0)
                    _log(f"Another indexing process (PID {pid}) is running. Exiting.")
                    return
                except OSError:
                    _log(f"Removing stale lock file (PID {pid} is not running).")
                    lock_path.unlink()
            except (ValueError, OSError):
                lock_path.unlink()

        lock_path.write_text(str(os.getpid()))
        try:
            self._do_run(force, verbose=verbose)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _do_run(self, force: bool, verbose: bool = False):
        t0 = time.monotonic()
        # 1. Discover files
        _log("Discovering files...")
        all_files = discover_files(self.root)
        _log(f"  Found {len(all_files)} files")

        # Delete existing DB when forcing — handles corrupted databases
        if force:
            db_path = get_db_path(self.root)
            if db_path.exists():
                db_path.unlink()
                # Also remove WAL/SHM files if they exist
                for suffix in ("-wal", "-shm"):
                    wal = db_path.parent / (db_path.name + suffix)
                    if wal.exists():
                        wal.unlink()

        with open_db(project_root=self.root) as conn:
            # 2. Determine what needs indexing
            if force:
                added = all_files
                modified = []
                removed = []
            else:
                added, modified, removed = get_changed_files(conn, all_files, self.root)

            total_changed = len(added) + len(modified) + len(removed)
            if total_changed == 0:
                _log("Index is up to date.")
                return

            _log(f"  {len(added)} added, {len(modified)} modified, {len(removed)} removed")

            # Remove deleted/modified files from DB (will cascade)
            for path in removed + modified:
                row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
                if row:
                    conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))

            # Get extractor factory
            get_extractor = _try_import_get_extractor()

            # 3-6. Parse, extract, and store for each file
            files_to_process = added + modified
            all_symbol_rows = {}   # symbol_id -> symbol dict
            all_references = []
            file_id_by_path = {}

            for i, rel_path in enumerate(files_to_process, 1):
                full_path = self.root / rel_path
                language = detect_language(rel_path)

                if (i % 100 == 0) or (i == len(files_to_process)):
                    _log(f"  Processing {i}/{len(files_to_process)} files...")

                # Read source for metadata
                try:
                    with open(full_path, "rb") as f:
                        source = f.read()
                except OSError as e:
                    if verbose:
                        _log(f"  Warning: Could not read {rel_path}: {e}")
                    continue

                line_count = _count_lines(source)
                complexity = _compute_complexity(source)
                try:
                    mtime = full_path.stat().st_mtime
                except OSError:
                    mtime = None
                fhash = file_hash(full_path)

                # Insert file record
                conn.execute(
                    "INSERT INTO files (path, language, hash, mtime, line_count) VALUES (?, ?, ?, ?, ?)",
                    (rel_path, language, fhash, mtime, line_count),
                )
                row = conn.execute("SELECT last_insert_rowid()").fetchone()
                if not row:
                    _log(f"  Warning: Failed to insert file record for {rel_path}")
                    continue
                file_id = row[0]
                file_id_by_path[rel_path] = file_id

                # Store file stats (complexity)
                conn.execute(
                    "INSERT OR REPLACE INTO file_stats (file_id, complexity) VALUES (?, ?)",
                    (file_id, complexity),
                )

                # Parse with tree-sitter
                tree, parsed_source, lang = parse_file(full_path, language)
                if tree is None:
                    continue

                # Get language extractor
                extractor = None
                if get_extractor is not None and lang is not None:
                    try:
                        extractor = get_extractor(lang)
                    except Exception as e:
                        if verbose:
                            _log(f"  Warning: No extractor for {lang}: {e}")
                        extractor = None

                if extractor is None:
                    continue

                # Extract symbols
                symbols = extract_symbols(tree, parsed_source, rel_path, extractor)

                for sym in symbols:
                    parent_id = None
                    if sym["parent_name"]:
                        # Look up parent in symbols already inserted for this file
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
                    }

                # Extract references
                refs = extract_references(tree, parsed_source, rel_path, extractor)
                for ref in refs:
                    ref["source_file"] = rel_path
                all_references.extend(refs)

                # Vue template scanning: find identifiers in <template> that
                # reference <script setup> bindings
                if rel_path.endswith(".vue"):
                    tpl_result = extract_vue_template(source)
                    if tpl_result:
                        tpl_content, tpl_start_line = tpl_result
                        known_names = {s["name"] for s in symbols}
                        tpl_refs = scan_template_references(
                            tpl_content, tpl_start_line, known_names, rel_path,
                        )
                        all_references.extend(tpl_refs)

                # Supplement: run generic extractor for inheritance refs
                # that Tier 1 extractors may miss
                if not isinstance(extractor, GenericExtractor) and language:
                    try:
                        generic = GenericExtractor(language=language)
                        generic_refs = generic.extract_references(tree, parsed_source, rel_path)
                        for ref in generic_refs:
                            if ref.get("kind") in ("inherits", "implements", "uses_trait"):
                                ref["source_file"] = rel_path
                                all_references.append(ref)
                    except Exception as e:
                        if verbose:
                            _log(f"  Warning: generic extractor failed for {rel_path}: {e}")

            # Also load existing symbols from DB (for incremental)
            if not force:
                existing_rows = conn.execute(
                    "SELECT s.id, s.file_id, s.name, s.qualified_name, s.kind, f.path as file_path "
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
                        }

            # Load all file_id_by_path from DB
            for row in conn.execute("SELECT id, path FROM files").fetchall():
                file_id_by_path[row["path"]] = row["id"]

            # Fix incremental edge loss: when files are modified, their old
            # symbols are deleted (CASCADE removes edges). We need to
            # re-extract references from unchanged files to restore
            # cross-file edges pointing to the modified files' new symbols.
            if not force and modified:
                processed_set = set(files_to_process) | set(removed)
                unchanged = [p for p in all_files if p not in processed_set]
                if unchanged:
                    _log(f"Re-extracting references from {len(unchanged)} unchanged files...")
                    # Delete ALL edges and file_edges — we rebuild them entirely
                    # from all_references (unchanged + modified files).
                    conn.execute("DELETE FROM edges")
                    conn.execute("DELETE FROM file_edges")

                    for rel_path in unchanged:
                        full_path = self.root / rel_path
                        language = detect_language(rel_path)
                        tree, parsed_source, lang = parse_file(full_path, language)
                        if tree is None:
                            continue
                        extractor = None
                        if get_extractor is not None and lang is not None:
                            try:
                                extractor = get_extractor(lang)
                            except Exception as e:
                                if verbose:
                                    _log(f"  Warning: no extractor for {lang}: {e}")
                        if extractor is None:
                            continue
                        # Call extract_symbols first to populate _pending_inherits
                        # (JS/TS extractors accumulate inheritance refs during symbol extraction)
                        try:
                            extractor.extract_symbols(tree, parsed_source, rel_path)
                        except Exception as e:
                            if verbose:
                                _log(f"  Warning: re-extract symbols failed for {rel_path}: {e}")
                        refs = extract_references(tree, parsed_source, rel_path, extractor)
                        for ref in refs:
                            ref["source_file"] = rel_path
                        all_references.extend(refs)
                        # Vue template scanning for unchanged files
                        if rel_path.endswith(".vue"):
                            from roam.index.parser import read_source
                            raw_source = read_source(full_path)
                            if raw_source:
                                tpl_result = extract_vue_template(raw_source)
                                if tpl_result:
                                    tpl_content, tpl_start_line = tpl_result
                                    syms = extractor.extract_symbols(tree, parsed_source, rel_path)
                                    known_names = {s["name"] for s in syms}
                                    tpl_refs = scan_template_references(
                                        tpl_content, tpl_start_line, known_names, rel_path,
                                    )
                                    all_references.extend(tpl_refs)
                        # Generic supplement for unchanged files too
                        if not isinstance(extractor, GenericExtractor) and language:
                            try:
                                generic = GenericExtractor(language=language)
                                generic_refs = generic.extract_references(tree, parsed_source, rel_path)
                                for ref in generic_refs:
                                    if ref.get("kind") in ("inherits", "implements", "uses_trait"):
                                        ref["source_file"] = rel_path
                                        all_references.append(ref)
                            except Exception as e:
                                if verbose:
                                    _log(f"  Warning: generic extractor failed for {rel_path}: {e}")

            # 6. Resolve references into edges
            _log("Resolving references...")
            symbols_by_name: dict[str, list[dict]] = {}
            for sym in all_symbol_rows.values():
                symbols_by_name.setdefault(sym["name"], []).append(sym)

            symbol_edges = resolve_references(all_references, symbols_by_name, file_id_by_path)

            # Store symbol edges
            conn.executemany(
                "INSERT INTO edges (source_id, target_id, kind, line) VALUES (?, ?, ?, ?)",
                [(e["source_id"], e["target_id"], e["kind"], e["line"]) for e in symbol_edges],
            )

            _log(f"  {len(symbol_edges)} symbol edges")

            # 7. Build file edges
            _log("Building file-level edges...")
            file_edges = build_file_edges(symbol_edges, all_symbol_rows)
            conn.executemany(
                "INSERT INTO file_edges (source_file_id, target_file_id, kind, symbol_count) "
                "VALUES (?, ?, ?, ?)",
                [(fe["source_file_id"], fe["target_file_id"], fe["kind"], fe["symbol_count"]) for fe in file_edges],
            )
            _log(f"  {len(file_edges)} file edges")

            # 8. Compute graph metrics (optional)
            build_symbol_graph, _store_metrics, _detect_clusters, _label_clusters, _store_clusters = _try_import_graph()
            G = None
            if build_symbol_graph is not None:
                _log("Computing graph metrics...")
                try:
                    G = build_symbol_graph(conn)
                    _store_metrics(conn, G)
                    metric_count = conn.execute("SELECT COUNT(*) FROM graph_metrics").fetchone()[0]
                    _log(f"  Metrics for {metric_count} symbols")
                except Exception as e:
                    _log(f"  Graph metrics failed: {e}")
            else:
                _log("Skipping graph metrics (module not available)")

            # 9. Git history analysis (optional)
            analyze_git = _try_import_git_stats()
            if analyze_git is not None:
                _log("Analyzing git history...")
                try:
                    analyze_git(conn, self.root)
                except Exception as e:
                    _log(f"  Git analysis failed: {e}")
            else:
                _log("Skipping git analysis (module not available)")

            # 10. Compute clusters (optional)
            if _detect_clusters is not None and G is not None:
                _log("Computing clusters...")
                try:
                    cluster_map = _detect_clusters(G)
                    labels = _label_clusters(cluster_map, conn)
                    _store_clusters(conn, cluster_map, labels)
                    _log(f"  {len(set(cluster_map.values()))} clusters")
                except Exception as e:
                    _log(f"  Clustering failed: {e}")
            else:
                _log("Skipping clustering (module not available)")

            # Log parse error summary
            from roam.index.parser import get_parse_error_summary
            error_summary = get_parse_error_summary()
            if error_summary:
                _log(f"  Parse issues: {error_summary}")

            # Summary
            elapsed = time.monotonic() - t0
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            _log(f"Done. {file_count} files, {sym_count} symbols, {edge_count} edges. ({elapsed:.1f}s)")
