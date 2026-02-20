"""Shared delta computation engine for pr-diff and budget commands.

Compares current metrics against a previous snapshot to compute
metric deltas, edge analysis, symbol changes, and footprint.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Direction classification
# ---------------------------------------------------------------------------

_HIGHER_IS_BETTER = {
    "health_score": True,
    "cycles": False,
    "god_components": False,
    "bottlenecks": False,
    "dead_exports": False,
    "layer_violations": False,
    "tangle_ratio": False,
    "avg_complexity": False,
    "brain_methods": False,
}


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
    """Return the content of *filepath* at *ref*, or None if it didn't exist."""
    cmd = ["git", "show", f"{ref}:{filepath}"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _extract_old_symbols(source: bytes, file_path: str) -> list[dict]:
    """Parse *source* bytes and extract symbols for *file_path*."""
    from roam.languages.registry import get_language_for_file, get_extractor_for_file
    from roam.index.symbols import extract_symbols
    from roam.index.parser import GRAMMAR_ALIASES

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
    except Exception:
        return []

    try:
        tree = parser.parse(source)
    except Exception:
        return []

    return extract_symbols(tree, source, file_path, extractor)


# ---------------------------------------------------------------------------
# Snapshot lookup
# ---------------------------------------------------------------------------

_METRIC_KEYS = [
    "files", "symbols", "edges", "cycles", "god_components",
    "bottlenecks", "dead_exports", "layer_violations", "health_score",
    "tangle_ratio", "avg_complexity", "brain_methods",
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
# Metric delta computation
# ---------------------------------------------------------------------------


def metric_delta(before: dict, after: dict) -> dict:
    """Compute per-metric deltas between *before* and *after* dicts.

    Returns {metric: {before, after, delta, pct_change, direction}}.
    """
    result = {}
    for metric, higher_better in _HIGHER_IS_BETTER.items():
        b = before.get(metric)
        a = after.get(metric)
        if b is None or a is None:
            continue

        b_val = float(b)
        a_val = float(a)
        delta = a_val - b_val

        if b_val != 0:
            pct_change = round((delta / abs(b_val)) * 100, 1)
        else:
            pct_change = 0.0 if delta == 0 else 100.0

        if delta == 0:
            direction = "unchanged"
        elif higher_better:
            direction = "improved" if delta > 0 else "degraded"
        else:
            direction = "degraded" if delta > 0 else "improved"

        result[metric] = {
            "before": b_val if isinstance(b_val, float) and b_val != int(b_val) else int(b_val),
            "after": a_val if isinstance(a_val, float) and a_val != int(a_val) else int(a_val),
            "delta": delta if isinstance(delta, float) and delta != int(delta) else int(delta),
            "pct_change": pct_change,
            "direction": direction,
        }
    return result


# ---------------------------------------------------------------------------
# Edge analysis
# ---------------------------------------------------------------------------


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
        "SELECT source_file_id, target_file_id, symbol_count "
        "FROM file_edges WHERE source_file_id IN ({ph})",
        changed_file_ids,
    )

    # Build file_id -> path map
    all_file_ids = set()
    for e in edges:
        all_file_ids.add(e["source_file_id"])
        all_file_ids.add(e["target_file_id"])
    all_file_ids.update(changed_file_ids)

    path_rows = batched_in(
        conn,
        "SELECT id, path FROM files WHERE id IN ({ph})",
        list(all_file_ids),
    )
    file_paths = {r["id"]: r["path"] for r in path_rows}

    # Build file_id -> cluster_label map (majority cluster per file)
    file_clusters: dict[int, str] = {}
    try:
        cluster_rows = batched_in(
            conn,
            "SELECT s.file_id, c.cluster_label "
            "FROM symbols s JOIN clusters c ON s.id = c.symbol_id "
            "WHERE s.file_id IN ({ph})",
            list(all_file_ids),
        )
        counts: dict[int, dict[str, int]] = {}
        for r in cluster_rows:
            fid = r["file_id"]
            label = r["cluster_label"]
            if fid not in counts:
                counts[fid] = {}
            counts[fid][label] = counts[fid].get(label, 0) + 1
        for fid, labels in counts.items():
            file_clusters[fid] = max(labels, key=labels.get)
    except Exception:
        pass

    # Build file_id -> layer map
    file_layers: dict[int, int] = {}
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers

        G = build_symbol_graph(conn)
        layer_map = detect_layers(G)
        if layer_map:
            # Map symbol layers to file layers (majority layer per file)
            sym_rows = batched_in(
                conn,
                "SELECT id, file_id FROM symbols WHERE file_id IN ({ph})",
                list(all_file_ids),
            )
            sym_to_file = {r["id"]: r["file_id"] for r in sym_rows}

            layer_counts: dict[int, dict[int, int]] = {}
            for sym_id, layer in layer_map.items():
                fid = sym_to_file.get(sym_id)
                if fid is None:
                    continue
                if fid not in layer_counts:
                    layer_counts[fid] = {}
                layer_counts[fid][layer] = layer_counts[fid].get(layer, 0) + 1
            for fid, layers_dict in layer_counts.items():
                file_layers[fid] = max(layers_dict, key=layers_dict.get)
    except Exception:
        pass

    # Flag cross-cluster and layer violations
    cross_cluster = []
    layer_violations = []

    for e in edges:
        src_id = e["source_file_id"]
        tgt_id = e["target_file_id"]
        src_path = file_paths.get(src_id, "?")
        tgt_path = file_paths.get(tgt_id, "?")

        # Cross-cluster
        src_cluster = file_clusters.get(src_id)
        tgt_cluster = file_clusters.get(tgt_id)
        if src_cluster and tgt_cluster and src_cluster != tgt_cluster:
            cross_cluster.append({
                "source": src_path,
                "target": tgt_path,
                "source_cluster": src_cluster,
                "target_cluster": tgt_cluster,
            })

        # Layer violation: lower layer depends on higher layer
        src_layer = file_layers.get(src_id)
        tgt_layer = file_layers.get(tgt_id)
        if src_layer is not None and tgt_layer is not None:
            if src_layer < tgt_layer:
                layer_violations.append({
                    "source": src_path,
                    "target": tgt_path,
                    "source_layer": src_layer,
                    "target_layer": tgt_layer,
                })

    return {
        "total_from_changed": len(edges),
        "cross_cluster": cross_cluster,
        "layer_violations": layer_violations,
    }


# ---------------------------------------------------------------------------
# Symbol changes
# ---------------------------------------------------------------------------


def symbol_changes(conn, root: Path, base_ref: str, changed_files: list[str]) -> dict:
    """Diff symbols between *base_ref* and current index for *changed_files*.

    Returns {added: [...], removed: [...], modified: [...]}.
    """
    added = []
    removed = []
    modified = []

    for fpath in changed_files:
        old_source = _git_show(root, base_ref, fpath)

        # Get current symbols from DB
        file_row = conn.execute(
            "SELECT id FROM files WHERE path = ?", (fpath,)
        ).fetchone()
        if not file_row:
            file_row = conn.execute(
                "SELECT id FROM files WHERE path LIKE ? LIMIT 1",
                (f"%{fpath}",),
            ).fetchone()

        current_syms = []
        if file_row:
            rows = conn.execute(
                "SELECT name, qualified_name, kind, signature, line_start "
                "FROM symbols WHERE file_id = ?",
                (file_row["id"],),
            ).fetchall()
            current_syms = [
                {
                    "name": r["name"],
                    "qualified_name": r["qualified_name"],
                    "kind": r["kind"],
                    "signature": r["signature"],
                    "line_start": r["line_start"],
                }
                for r in rows
            ]

        if old_source is None:
            # New file — all symbols are added
            for s in current_syms:
                added.append({
                    "name": s["name"],
                    "kind": s["kind"],
                    "file": fpath,
                    "line": s.get("line_start"),
                })
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
            added.append({
                "name": s["name"],
                "kind": s["kind"],
                "file": fpath,
                "line": s.get("line_start"),
            })

        # Removed
        for k in old_keys - new_keys:
            s = old_by_key[k]
            removed.append({
                "name": s["name"],
                "kind": s["kind"],
                "file": fpath,
            })

        # Modified (signature changed)
        for k in old_keys & new_keys:
            old_sig = (old_by_key[k].get("signature") or "").strip()
            new_sig = (new_by_key[k].get("signature") or "").strip()
            if old_sig and new_sig and old_sig != new_sig:
                modified.append({
                    "name": new_by_key[k]["name"],
                    "kind": new_by_key[k]["kind"],
                    "file": fpath,
                    "line": new_by_key[k].get("line_start"),
                })

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
