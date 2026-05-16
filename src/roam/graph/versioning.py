"""Graph-level versioning + diff primitives for `graph-diff` / `architecture-drift`.

R23 — "Not just what changed in code, but what changed in the SYSTEM STRUCTURE."

This module is the pure-functions core: snapshot the current symbol graph into
a portable dict, then set-diff two such snapshots into a ``GraphDiff``.
Commands ``graph-diff`` and ``architecture-drift`` wrap this with CLI plumbing
and trend/series math.

Design notes
------------
* The snapshot dict is **portable JSON** — no DB handles, no NetworkX graphs.
  This lets us persist a snapshot to ``.roam/snapshots/<sha>.json`` and rehydrate
  it later for cross-commit comparison (option B from the R23 spec).
* Symbols are keyed by a stable ``qualified_name + kind`` string, **not** by
  the database row id, so re-indexing the same code yields the same id keys.
  DB ids change across re-indexes; we cannot rely on them.
* The in/out-degree shift threshold is intentionally hybrid: ``|delta| >= 2``
  catches small absolute changes on quiet symbols (1 -> 3 doubles fan-in even
  though the ratio is large), while ``|delta| >= 0.25 * old`` catches large
  proportional changes on already-busy symbols (40 -> 50 is +25%). A symbol
  must clear BOTH 2 absolute AND 25% relative to count as a shift -- otherwise
  every tiny perturbation swamps the report.
* "Likely moves" deliberately fuses removed-and-added symbols with the same
  name across files. Confidence is HIGH when name + kind both match, MEDIUM
  when only the name matches. We never emit a "move" for symbols that already
  exist on both sides under a different file -- that would conflate moves
  with renames.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Snapshot persistence layout
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = ".roam/snapshots"


def snapshot_dir(root: Path) -> Path:
    """Return the directory where graph snapshots live for *root*."""
    return Path(root) / SNAPSHOT_DIR


# ---------------------------------------------------------------------------
# Snapshot — current DB graph -> portable dict
# ---------------------------------------------------------------------------


def _symbol_key(name: str | None, kind: str | None, file_path: str | None) -> str:
    """Stable identity for a symbol across re-indexes.

    Combines name + kind + file path. ``qualified_name`` would be ideal but
    not every language extractor populates it consistently. Falling back to
    file path keeps the key collision-resistant for same-named symbols in
    different files (e.g. ``__init__`` everywhere).
    """
    return f"{name or '?'}::{kind or '?'}::{file_path or '?'}"


def snapshot_graph(conn: sqlite3.Connection) -> dict:
    """Snapshot the current DB graph into a portable, JSON-serializable dict.

    Shape::

        {
            "symbols": {sym_key: {name, kind, file, in_degree, out_degree, ...}},
            "edges": [{source, target, kind}, ...],
            "cycles": [[sym_key1, sym_key2, ...], ...],
            "layers": {sym_key: layer_number},
            "metrics": {
                "symbol_count": N,
                "edge_count": M,
                "cycle_count": K,
                "layer_count": L,
            },
        }

    Symbols are keyed by ``_symbol_key`` (stable across re-indexes); raw DB
    ids are kept on each entry so callers can re-resolve to current rows.
    """
    # Pull every symbol + its file path.
    rows = conn.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "ORDER BY s.id"
    ).fetchall()

    id_to_key: dict[int, str] = {}
    symbols: dict[str, dict] = {}
    for r in rows:
        sid = r["id"]
        name = r["name"]
        kind = r["kind"]
        file_path = r["file_path"]
        key = _symbol_key(name, kind, file_path)
        # If a collision happens (same name+kind+file appears twice), keep the
        # first and uniquify the second with the DB id suffix so set ops stay
        # honest. In practice this is rare.
        if key in symbols:
            key = f"{key}#id={sid}"
        id_to_key[sid] = key
        symbols[key] = {
            "name": name,
            "kind": kind,
            "file": file_path,
            "qualified_name": r["qualified_name"],
            "db_id": sid,
            "in_degree": 0,
            "out_degree": 0,
        }

    # Pull edges and accumulate per-symbol degree counts.
    edge_rows = conn.execute("SELECT source_id, target_id, kind FROM edges ORDER BY source_id, target_id").fetchall()
    edges: list[dict] = []
    for er in edge_rows:
        src_id = er["source_id"]
        tgt_id = er["target_id"]
        src_key = id_to_key.get(src_id)
        tgt_key = id_to_key.get(tgt_id)
        if src_key is None or tgt_key is None:
            # Edge points at a stripped symbol — skip rather than corrupt the
            # snapshot. The full set comparison stays honest.
            continue
        edges.append({"source": src_key, "target": tgt_key, "kind": er["kind"]})
        symbols[src_key]["out_degree"] += 1
        symbols[tgt_key]["in_degree"] += 1

    # Cycles: condense to symbol keys.
    cycles: list[list[str]] = []
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.cycles import find_cycles

        G = build_symbol_graph(conn)
        for scc in find_cycles(G):
            cycle_keys = sorted({id_to_key[i] for i in scc if i in id_to_key})
            if len(cycle_keys) >= 2:
                cycles.append(cycle_keys)
    except Exception:
        # Snapshot must always succeed even when graph algorithms blow up.
        cycles = []

    # Layers: condense to {sym_key: layer}.
    layers: dict[str, int] = {}
    try:
        from roam.graph.builder import build_symbol_graph
        from roam.graph.layers import detect_layers

        G = build_symbol_graph(conn)
        for sid, layer in detect_layers(G).items():
            key = id_to_key.get(sid)
            if key is not None:
                layers[key] = int(layer)
    except Exception:
        layers = {}

    return {
        "symbols": symbols,
        "edges": edges,
        "cycles": cycles,
        "layers": layers,
        "metrics": {
            "symbol_count": len(symbols),
            "edge_count": len(edges),
            "cycle_count": len(cycles),
            "layer_count": (max(layers.values()) + 1) if layers else 0,
        },
    }


# ---------------------------------------------------------------------------
# GraphDiff dataclass + diff_graphs
# ---------------------------------------------------------------------------


@dataclass
class GraphDiff:
    """Structured delta between two graph snapshots.

    ``total_signal_count`` is a one-number summary callers can put in a
    verdict line without needing to know the field schema.
    """

    symbols_added: list[str] = field(default_factory=list)
    symbols_removed: list[str] = field(default_factory=list)
    edges_added: list[tuple[str, str, str]] = field(default_factory=list)
    edges_removed: list[tuple[str, str, str]] = field(default_factory=list)
    in_degree_shifts: list[dict] = field(default_factory=list)
    out_degree_shifts: list[dict] = field(default_factory=list)
    new_cycles: list[list[str]] = field(default_factory=list)
    removed_cycles: list[list[str]] = field(default_factory=list)
    layer_changes: list[dict] = field(default_factory=list)
    likely_moves: list[dict] = field(default_factory=list)
    total_signal_count: int = 0


# Hybrid threshold knobs. Documented above; lifted as constants for tests.
DEGREE_SHIFT_ABS_MIN = 2
DEGREE_SHIFT_REL_MIN = 0.25


def _edge_key(edge: dict) -> tuple[str, str, str]:
    return (edge.get("source", ""), edge.get("target", ""), edge.get("kind", ""))


def _detect_degree_shifts(before_syms: dict, after_syms: dict, attr: str) -> list[dict]:
    """Symbols whose ``attr`` (in_degree / out_degree) shifted notably.

    Both absolute (>= ``DEGREE_SHIFT_ABS_MIN``) AND relative
    (>= ``DEGREE_SHIFT_REL_MIN`` of the old value) thresholds must clear;
    this prevents tiny perturbations on every node from flooding the report.
    """
    shifts: list[dict] = []
    for key in before_syms.keys() & after_syms.keys():
        before_val = int(before_syms[key].get(attr, 0))
        after_val = int(after_syms[key].get(attr, 0))
        delta = after_val - before_val
        if delta == 0:
            continue
        abs_delta = abs(delta)
        if abs_delta < DEGREE_SHIFT_ABS_MIN:
            continue
        # Relative check; for previously-zero values, any abs >= MIN qualifies.
        if before_val > 0 and abs_delta < before_val * DEGREE_SHIFT_REL_MIN:
            continue
        shifts.append(
            {
                "symbol": key,
                "before": before_val,
                "after": after_val,
                "delta": delta,
            }
        )
    # Most-shifted first; ties broken by symbol key for determinism.
    shifts.sort(key=lambda s: (-abs(s["delta"]), s["symbol"]))
    return shifts


def _detect_layer_changes(before: dict, after: dict) -> list[dict]:
    """Symbols whose topological layer changed across the snapshots."""
    before_layers = before.get("layers") or {}
    after_layers = after.get("layers") or {}
    out: list[dict] = []
    for key in before_layers.keys() & after_layers.keys():
        lb = int(before_layers[key])
        la = int(after_layers[key])
        if lb != la:
            out.append({"symbol": key, "layer_before": lb, "layer_after": la})
    out.sort(key=lambda s: (-abs(s["layer_after"] - s["layer_before"]), s["symbol"]))
    return out


def _detect_likely_moves(
    before_syms: dict,
    after_syms: dict,
    removed_keys: set[str],
    added_keys: set[str],
) -> list[dict]:
    """Cross-reference removed and added symbols by ``name`` / ``kind``.

    For each removed symbol, try to find an added symbol with matching name
    (and optionally kind). HIGH confidence when both name + kind match;
    MEDIUM when only name matches. A symbol that simultaneously exists on
    both sides under a different file is treated as a separate signal — we
    only flag pure move candidates here.
    """
    # Index added-side by name for quick lookup.
    added_by_name: dict[str, list[str]] = {}
    for k in added_keys:
        meta = after_syms.get(k) or {}
        nm = meta.get("name") or ""
        if not nm:
            continue
        added_by_name.setdefault(nm, []).append(k)

    moves: list[dict] = []
    used_added: set[str] = set()
    for rkey in sorted(removed_keys):
        rmeta = before_syms.get(rkey) or {}
        rname = rmeta.get("name")
        rkind = rmeta.get("kind")
        rfile = rmeta.get("file")
        if not rname:
            continue
        candidates = added_by_name.get(rname, [])
        # Prefer kind matches first.
        high_match = None
        med_match = None
        for ck in candidates:
            if ck in used_added:
                continue
            ameta = after_syms.get(ck) or {}
            if ameta.get("file") == rfile:
                # Same file — not a "move" in the structural sense.
                continue
            if rkind and ameta.get("kind") == rkind:
                high_match = ck
                break
            if med_match is None:
                med_match = ck
        picked = high_match or med_match
        if picked is None:
            continue
        used_added.add(picked)
        ameta = after_syms.get(picked) or {}
        moves.append(
            {
                "symbol": rname,
                "kind": rkind,
                "from_file": rfile,
                "to_file": ameta.get("file"),
                "confidence": "high" if picked == high_match else "medium",
            }
        )
    return moves


def _frozen_cycles(cycles: list[list[str]]) -> set[frozenset[str]]:
    return {frozenset(c) for c in cycles or []}


def diff_graphs(before: dict, after: dict) -> GraphDiff:
    """Compute the structural diff between two snapshots.

    Both arguments must be the dict shape produced by :func:`snapshot_graph`.
    All comparisons are pure set / dict ops — O(N + E) over the two snapshots,
    plus an O(L) layer comparison. No graph rebuild required.
    """
    before_syms = (before or {}).get("symbols") or {}
    after_syms = (after or {}).get("symbols") or {}

    before_keys = set(before_syms)
    after_keys = set(after_syms)

    added_keys = after_keys - before_keys
    removed_keys = before_keys - after_keys

    before_edges = {_edge_key(e) for e in (before or {}).get("edges") or []}
    after_edges = {_edge_key(e) for e in (after or {}).get("edges") or []}

    edges_added = sorted(after_edges - before_edges)
    edges_removed = sorted(before_edges - after_edges)

    in_degree_shifts = _detect_degree_shifts(before_syms, after_syms, "in_degree")
    out_degree_shifts = _detect_degree_shifts(before_syms, after_syms, "out_degree")

    before_cycles = _frozen_cycles((before or {}).get("cycles") or [])
    after_cycles = _frozen_cycles((after or {}).get("cycles") or [])
    new_cycles = [sorted(c) for c in after_cycles - before_cycles]
    removed_cycles = [sorted(c) for c in before_cycles - after_cycles]

    layer_changes = _detect_layer_changes(before, after)

    likely_moves = _detect_likely_moves(before_syms, after_syms, removed_keys, added_keys)

    # If we matched a removed symbol to an added one with HIGH confidence, drop
    # both from the raw add/remove lists -- otherwise the same move ends up
    # signalled three times. MEDIUM-confidence moves stay on both lists so the
    # caller can still see the rename ambiguity.
    moved_from = {(m["symbol"], m["from_file"]) for m in likely_moves if m["confidence"] == "high"}
    moved_to = {(m["symbol"], m["to_file"]) for m in likely_moves if m["confidence"] == "high"}

    def _key_pair(k: str, syms: dict) -> tuple[str, str]:
        meta = syms.get(k) or {}
        return (meta.get("name") or "", meta.get("file") or "")

    pruned_removed = sorted(k for k in removed_keys if _key_pair(k, before_syms) not in moved_from)
    pruned_added = sorted(k for k in added_keys if _key_pair(k, after_syms) not in moved_to)

    total = (
        len(pruned_added)
        + len(pruned_removed)
        + len(edges_added)
        + len(edges_removed)
        + len(in_degree_shifts)
        + len(out_degree_shifts)
        + len(new_cycles)
        + len(removed_cycles)
        + len(layer_changes)
        + len(likely_moves)
    )

    return GraphDiff(
        symbols_added=pruned_added,
        symbols_removed=pruned_removed,
        edges_added=edges_added,
        edges_removed=edges_removed,
        in_degree_shifts=in_degree_shifts,
        out_degree_shifts=out_degree_shifts,
        new_cycles=new_cycles,
        removed_cycles=removed_cycles,
        layer_changes=layer_changes,
        likely_moves=likely_moves,
        total_signal_count=total,
    )


# ---------------------------------------------------------------------------
# Snapshot persistence helpers (used by `graph-diff` + `architecture-drift`)
# ---------------------------------------------------------------------------


def list_snapshot_files(root: Path) -> list[Path]:
    """Return all ``.json`` snapshot files in ``.roam/snapshots/``, oldest first."""
    sdir = snapshot_dir(root)
    if not sdir.exists():
        return []
    files = [p for p in sdir.iterdir() if p.is_file() and p.suffix == ".json"]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def write_snapshot(root: Path, snap: dict, label: str | None = None) -> Path:
    """Persist *snap* to ``.roam/snapshots/<label-or-timestamp>.json``.

    Returns the resulting path. Creates the directory on demand.
    """
    import json
    import time

    sdir = snapshot_dir(root)
    sdir.mkdir(parents=True, exist_ok=True)
    if not label:
        label = f"snap-{int(time.time())}"
    # Sanitise label so callers can pass commit shas / branch names freely.
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in label)
    path = sdir / f"{safe}.json"
    path.write_text(json.dumps(snap, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_snapshot(path: Path) -> dict | None:
    """Read a JSON snapshot from disk, returning ``None`` on any failure."""
    import json

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
