"""Shared delta computation engine for pr-diff and budget commands.

Compares current metrics against a previous snapshot to compute
metric deltas, edge analysis, symbol changes, and footprint.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from roam.graph.simulate import metric_delta as metric_delta

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def resolve_base_commit(root: Path, base_ref: str) -> str | None:
    """Resolve *base_ref* to a short commit hash, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", base_ref],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _git_show(root: Path, ref: str, filepath: str) -> bytes | None:
    """Return the content of *filepath* at *ref*, or None if it didn't exist.

    Thin delegating alias for :func:`roam.commands.changed_files.git_show_at_ref`.
    Kept under the original private name so the module-internal call sites stay
    grep-stable; the canonical implementation lives in ``changed_files`` per
    the W-vibe-check DRY consolidation.
    """
    from roam.commands.changed_files import git_show_at_ref

    return git_show_at_ref(root, ref, filepath)


def _extract_old_symbols(source: bytes, file_path: str) -> list[dict]:
    """Parse *source* bytes and extract symbols for *file_path*."""
    from roam.index.parser import GRAMMAR_ALIASES
    from roam.index.symbols import extract_symbols
    from roam.languages.registry import get_extractor_for_file, get_language_for_file

    language = get_language_for_file(file_path)
    if language is None:
        return []

    extractor = get_extractor_for_file(file_path)
    if extractor is None:
        return []

    grammar = GRAMMAR_ALIASES.get(language, language)
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(grammar)
    except LookupError:
        return []

    try:
        tree = parser.parse(source)
    except TypeError:
        return []

    return extract_symbols(tree, source, file_path, extractor)


# ---------------------------------------------------------------------------
# Snapshot lookup
# ---------------------------------------------------------------------------

_METRIC_KEYS = [
    "files",
    "symbols",
    "edges",
    "cycles",
    "god_components",
    "bottlenecks",
    "dead_exports",
    "layer_violations",
    "health_score",
    "tangle_ratio",
    "avg_complexity",
    "brain_methods",
]


def find_before_snapshot(conn, root: Path, base_ref: str | None = None) -> dict | None:
    """Find the best matching snapshot to use as the 'before' state.

    Strategy:
    1. If *base_ref* given, resolve to commit hash and match snapshots.git_commit
    2. Fallback: latest snapshot by timestamp
    3. Return None if no snapshots exist
    """
    if base_ref:
        commit_hash = resolve_base_commit(root, base_ref)
        if commit_hash:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE git_commit = ? LIMIT 1",
                (commit_hash,),
            ).fetchone()
            if row:
                return {k: row[k] for k in row.keys()}

    # Fallback: latest snapshot
    row = conn.execute(
        "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1",
    ).fetchone()
    if row:
        return {k: row[k] for k in row.keys()}
    return None


# ---------------------------------------------------------------------------
# Edge analysis
# ---------------------------------------------------------------------------


def _edge_context_file_ids(edges, changed_file_ids: list[int]) -> list[int]:
    """Collect every file id needed to explain changed-file edge records."""
    file_ids = set(changed_file_ids)
    for edge in edges:
        file_ids.add(edge["source_file_id"])
        file_ids.add(edge["target_file_id"])
    return list(file_ids)


def _majority_annotation_by_file(rows, value_key: str) -> dict[int, object]:
    """Collapse symbol-level annotations to the file-level signal edges need."""
    counts: dict[int, dict[object, int]] = {}
    for row in rows:
        fid = row["file_id"]
        value = row[value_key]
        counts.setdefault(fid, {})
        counts[fid][value] = counts[fid].get(value, 0) + 1
    return {fid: max(values, key=values.get) for fid, values in counts.items()}


def _best_effort_clusters_for_edge_context(conn, file_ids: list[int]) -> dict[int, object]:
    """Keep edge analysis usable when optional cluster data is unavailable."""
    from roam.db.connection import batched_in

    try:
        rows = batched_in(
            conn,
            "SELECT s.file_id, c.cluster_label "
            "FROM symbols s JOIN clusters c ON s.id = c.symbol_id "
            "WHERE s.file_id IN ({ph})",
            file_ids,
        )
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return {}
    return _majority_annotation_by_file(rows, "cluster_label")


def _symbol_file_ids_for_edge_context(conn, file_ids: list[int]) -> dict[int, int]:
    """Map symbol-layer results back to the files that changed edges mention."""
    from roam.db.connection import batched_in

    rows = batched_in(
        conn,
        "SELECT id, file_id FROM symbols WHERE file_id IN ({ph})",
        file_ids,
    )
    return {row["id"]: row["file_id"] for row in rows}


def _layer_rows_for_file_majorities(layer_map: dict, sym_to_file: dict[int, int]) -> list[dict]:
    """Convert symbol-layer assignments into rows for file-majority collapse."""
    rows = []
    for sym_id, layer in layer_map.items():
        fid = sym_to_file.get(sym_id)
        if fid is not None:
            rows.append({"file_id": fid, "layer": layer})
    return rows


def _best_effort_layers_for_edge_context(conn, file_ids: list[int]) -> dict[int, object]:
    """Keep edge analysis usable when optional graph layering is unavailable."""
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers

        layer_map = detect_layers(build_symbol_graph(conn))
        if not layer_map:
            return {}

        sym_to_file = _symbol_file_ids_for_edge_context(conn, file_ids)
        rows = _layer_rows_for_file_majorities(layer_map, sym_to_file)
    except (ImportError, sqlite3.Error, KeyError, RuntimeError, TypeError, ValueError):
        return {}
    return _majority_annotation_by_file(rows, "layer")


def _cross_cluster_record(edge, file_paths: dict[int, str], file_clusters: dict[int, object]) -> dict | None:
    """Report changed edges that cross recovered cluster boundaries."""
    src_id = edge["source_file_id"]
    tgt_id = edge["target_file_id"]
    src_cluster = file_clusters.get(src_id)
    tgt_cluster = file_clusters.get(tgt_id)
    if not src_cluster or not tgt_cluster or src_cluster == tgt_cluster:
        return None
    return {
        "source": file_paths.get(src_id, "?"),
        "target": file_paths.get(tgt_id, "?"),
        "source_cluster": src_cluster,
        "target_cluster": tgt_cluster,
    }


def _layer_violation_record(edge, file_paths: dict[int, str], file_layers: dict[int, object]) -> dict | None:
    """Report changed edges that reverse the recovered layer direction."""
    src_id = edge["source_file_id"]
    tgt_id = edge["target_file_id"]
    src_layer = file_layers.get(src_id)
    tgt_layer = file_layers.get(tgt_id)
    if src_layer is None or tgt_layer is None or src_layer >= tgt_layer:
        return None
    return {
        "source": file_paths.get(src_id, "?"),
        "target": file_paths.get(tgt_id, "?"),
        "source_layer": src_layer,
        "target_layer": tgt_layer,
    }


def _classify_edges_with_available_context(
    edges,
    file_paths: dict[int, str],
    file_clusters: dict[int, object],
    file_layers: dict[int, object],
) -> tuple[list[dict], list[dict]]:
    """Separate structural risks while preserving partial optional context."""
    cross_cluster = []
    layer_violations = []
    for edge in edges:
        cross_cluster_record = _cross_cluster_record(edge, file_paths, file_clusters)
        if cross_cluster_record:
            cross_cluster.append(cross_cluster_record)

        layer_violation_record = _layer_violation_record(edge, file_paths, file_layers)
        if layer_violation_record:
            layer_violations.append(layer_violation_record)
    return cross_cluster, layer_violations


def edge_analysis(conn, changed_file_ids: list[int]) -> dict:
    """Analyse dependency edges from changed files.

    Returns {total_from_changed, cross_cluster, layer_violations}.
    """
    if not changed_file_ids:
        return {"total_from_changed": 0, "cross_cluster": [], "layer_violations": []}

    from roam.db.connection import batched_in

    # Query file_edges from changed files
    edges = batched_in(
        conn,
        "SELECT source_file_id, target_file_id, symbol_count FROM file_edges WHERE source_file_id IN ({ph})",
        changed_file_ids,
    )

    all_file_ids = _edge_context_file_ids(edges, changed_file_ids)

    path_rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE id IN ({ph})",
        all_file_ids,
    )
    file_paths = {r["id"]: r["path"] for r in path_rows}

    file_clusters = _best_effort_clusters_for_edge_context(conn, all_file_ids)
    file_layers = _best_effort_layers_for_edge_context(conn, all_file_ids)
    cross_cluster, layer_violations = _classify_edges_with_available_context(
        edges,
        file_paths,
        file_clusters,
        file_layers,
    )

    return {
        "total_from_changed": len(edges),
        "cross_cluster": cross_cluster,
        "layer_violations": layer_violations,
    }


# ---------------------------------------------------------------------------
# Symbol changes
# ---------------------------------------------------------------------------


def _load_current_symbols_without_loop_queries(conn, changed_files: list[str]) -> dict[str, list[dict]]:
    """Batch current-symbol lookup while preserving exact-then-suffix file resolution."""
    from roam.db.connection import batched_in

    if not changed_files:
        return {}

    file_id_by_changed_path: dict[str, int] = {}

    exact_rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE path IN ({ph})",
        changed_files,
    )
    for row in exact_rows:
        file_id_by_changed_path[row["path"]] = row["id"]

    unresolved_paths = [fpath for fpath in changed_files if fpath not in file_id_by_changed_path]
    if unresolved_paths:
        file_rows = conn.execute("SELECT id, path FROM files ORDER BY id").fetchall()
        for fpath in unresolved_paths:
            for row in file_rows:
                if row["path"].endswith(fpath):
                    file_id_by_changed_path[fpath] = row["id"]
                    break

    file_ids = sorted(set(file_id_by_changed_path.values()))
    symbol_rows = batched_in(
        conn,
        "SELECT file_id, name, qualified_name, kind, signature, line_start FROM symbols WHERE file_id IN ({ph})",
        file_ids,
    )

    symbols_by_file_id: dict[int, list[dict]] = {}
    for row in symbol_rows:
        symbols_by_file_id.setdefault(row["file_id"], []).append(
            {
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "kind": row["kind"],
                "signature": row["signature"],
                "line_start": row["line_start"],
            }
        )

    return {
        fpath: symbols_by_file_id.get(file_id_by_changed_path[fpath], [])
        for fpath in changed_files
        if fpath in file_id_by_changed_path
    }


def symbol_changes(conn, root: Path, base_ref: str, changed_files: list[str]) -> dict:
    """Diff symbols between *base_ref* and current index for *changed_files*.

    Returns {added: [...], removed: [...], modified: [...]}.
    """
    added = []
    removed = []
    modified = []
    current_symbols_by_path = _load_current_symbols_without_loop_queries(conn, changed_files)

    for fpath in changed_files:
        old_source = _git_show(root, base_ref, fpath)
        current_syms = current_symbols_by_path.get(fpath, [])

        if old_source is None:
            # New file — all symbols are added
            for s in current_syms:
                added.append(
                    {
                        "name": s["name"],
                        "kind": s["kind"],
                        "file": fpath,
                        "line": s.get("line_start"),
                    }
                )
            continue

        old_syms = _extract_old_symbols(old_source, fpath)

        # Build lookup by qualified_name or name
        def _key(s):
            return s.get("qualified_name") or s.get("name", "")

        old_by_key = {_key(s): s for s in old_syms}
        new_by_key = {_key(s): s for s in current_syms}

        old_keys = set(old_by_key)
        new_keys = set(new_by_key)

        # Added
        for k in new_keys - old_keys:
            s = new_by_key[k]
            added.append(
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "file": fpath,
                    "line": s.get("line_start"),
                }
            )

        # Removed
        for k in old_keys - new_keys:
            s = old_by_key[k]
            removed.append(
                {
                    "name": s["name"],
                    "kind": s["kind"],
                    "file": fpath,
                }
            )

        # Modified (signature changed)
        for k in old_keys & new_keys:
            old_sig = (old_by_key[k].get("signature") or "").strip()
            new_sig = (new_by_key[k].get("signature") or "").strip()
            if old_sig and new_sig and old_sig != new_sig:
                modified.append(
                    {
                        "name": new_by_key[k]["name"],
                        "kind": new_by_key[k]["kind"],
                        "file": fpath,
                        "line": new_by_key[k].get("line_start"),
                    }
                )

    return {"added": added, "removed": removed, "modified": modified}


# ---------------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------------


def compute_footprint(conn, changed_file_ids: list[int]) -> dict:
    """Compute how much of the graph the changed files touch.

    Returns {files_changed, files_total, files_pct,
             symbols_changed, symbols_total, symbols_pct}.
    """
    files_total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    symbols_total = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    files_changed = len(changed_file_ids)
    files_pct = round(files_changed / files_total * 100, 1) if files_total else 0.0

    symbols_changed = 0
    if changed_file_ids:
        from roam.db.connection import batched_in

        rows = batched_in(
            conn,
            "SELECT COUNT(*) AS cnt FROM symbols WHERE file_id IN ({ph})",
            changed_file_ids,
        )
        for r in rows:
            symbols_changed += r["cnt"]

    symbols_pct = round(symbols_changed / symbols_total * 100, 1) if symbols_total else 0.0

    return {
        "files_changed": files_changed,
        "files_total": files_total,
        "files_pct": files_pct,
        "symbols_changed": symbols_changed,
        "symbols_total": symbols_total,
        "symbols_pct": symbols_pct,
    }
