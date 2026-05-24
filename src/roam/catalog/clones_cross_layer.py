"""Cross-layer clone detector (W856) — Fowler-style DRY across architectural layers.

The existing literal-clone detectors (``W95`` token-bag SHA-1 clone-pairs,
``W855`` rename-invariant DECKARD characteristic vectors) catch in-layer
copy-paste — two functions whose ASTs look alike. They do NOT catch the
real-world DRY debt class identified in
``(internal memo)`` §2.4: the same domain
transformation re-implemented at **different architectural layers**
(controller, service, repository) where the bodies share zero tokens but
the underlying call structure routes through the same domain primitives.

Worked example
--------------
A controller ``OrderController.computeTotal`` and a service
``OrderService.calculateAmount`` both call ``apply_tax``,
``apply_discount`` and ``sum_line_items``. Their bodies look nothing
alike — the controller pulls JSON params and returns an HTTP response,
the service consumes a domain object and returns a ``Decimal`` — but
they duplicate the **same domain logic**. Token-bag detectors miss this
pair. Their *call-target multisets* are nearly identical, which is the
signal this detector exploits.

Algorithm
---------
1. Classify each symbol's layer from its file path. Buckets are
   coarse-grained heuristics that work across Django / Laravel / Spring /
   Rails / Express / ASP.NET — see ``_LAYER_PATTERNS`` below.
2. Pull each function/method symbol's outbound ``call`` edges and convert
   the callee target ids to a multiset of **callee names** (alpha-invariant
   across renames + cross-language).
3. For each unordered pair of layers (controller&service, service&repo,
   ...) compute the Jaccard similarity of every cross-layer pair's
   call-name multisets.
4. Threshold: ``jaccard >= 0.7`` AND ``len(shared_callees) >= 3``. The
   ``>= 3`` gate prevents a tiny-stub coincidence — a controller calling
   one service helper is correct delegation, not a clone.
5. Emit one finding per matched pair carrying the shared callees,
   per-layer file paths, and the precise Jaccard score.

Confidence tier: ``structural`` — edge-derived, deterministic, no regex
on names. The signal is "two symbols call the same set of named
primitives at the graph level" — that's a structural property of the
call graph, not a name-matching heuristic.

LAW-4 anchor terminals used by the description string: ``callees``
(see ``src/roam/output/formatter.py:concrete_plural_terminals``).

Why a separate module
---------------------
``smells.py`` is the canonical home for Fowler-family detectors but the
file is already 2900+ lines and frequently touched. W856 lands as its
own module, ``ALL_DETECTORS``-imported, mirroring W855
(``clones_rename_invariant.py``) and W857 (``parallel_hierarchy.py``).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from roam.catalog._shared import loc as _loc
from roam.catalog._shared import make_smell_finding
from roam.db.edge_kinds import CALL_EDGE_KINDS

# Detector identity constants (W81 versioning discipline). Bump on shape
# changes so findings-registry consumers can spot rows from a stale shape.
CROSS_LAYER_CLONE_DETECTOR = "cross-layer-clone"
CROSS_LAYER_CLONE_DETECTOR_VERSION = 1

# Defensive per-layer-pair Jaccard budget. The cross-layer scan compares
# every caller in layer A against every caller in layer B — an O(|A|*|B|)
# double loop per layer pair. On a LAYERED repo at Django / Spring scale
# (thousands of controllers and services), that product explodes. This cap
# bounds the comparisons performed for ONE layer pair; when a pair would
# exceed it, the scan truncates that pair's comparison and emits a sentinel
# finding (kind ``cross_layer_clone_truncated``) so the truncation is
# DISCLOSED, never silent (Pattern-1 "structured signal lost" discipline).
# 4_000_000 = a 2000x2000 layer pair — comfortably above any real layered
# repo while still bounding worst-case work. Tunable via the
# ``ROAM_CROSS_LAYER_PAIR_BUDGET`` env var for operators on pathological
# corpora. roam-code itself is not a layered repo, so this is a no-op here.
_DEFAULT_CROSS_LAYER_PAIR_BUDGET = 4_000_000


def _cross_layer_pair_budget() -> int:
    """Resolve the per-layer-pair comparison budget.

    Reads ``ROAM_CROSS_LAYER_PAIR_BUDGET`` when set to a positive integer;
    falls back to ``_DEFAULT_CROSS_LAYER_PAIR_BUDGET`` on absent / malformed
    / non-positive values (defensive default discipline — a bad env var must
    not disable the cap entirely).
    """
    import os

    raw = os.environ.get("ROAM_CROSS_LAYER_PAIR_BUDGET")
    if raw is None:
        return _DEFAULT_CROSS_LAYER_PAIR_BUDGET
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CROSS_LAYER_PAIR_BUDGET
    return val if val > 0 else _DEFAULT_CROSS_LAYER_PAIR_BUDGET


# ---------------------------------------------------------------------------
# Layer classification
# ---------------------------------------------------------------------------

# Path-fragment heuristics. Each layer is a tuple of path fragments; if
# ANY fragment appears as a path segment (forward slash on either side or
# a leading match), the file maps to that layer. The order matters when
# multiple layers could match — ``controller`` precedes ``view`` because
# Spring's ``@Controller`` files often sit under ``views/`` and we want
# to flag them as controllers. Likewise ``repository`` precedes
# ``service`` because Rails-style ``app/services/`` may contain a
# repository facade — biasing toward the more specific name first keeps
# the buckets clean.
_LAYER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "controller",
        (
            "/controllers/",
            "/controller/",
            "/routes/",
            "/api/",
            "/handlers/",
            "/endpoints/",
        ),
    ),
    (
        "repository",
        (
            "/repositories/",
            "/repository/",
            "/repos/",
            "/dao/",
            "/data/",
            "/models/",
            "/persistence/",
        ),
    ),
    (
        "service",
        (
            "/services/",
            "/service/",
            "/usecases/",
            "/use_cases/",
            "/business/",
            "/domain/",
        ),
    ),
    (
        "view",
        (
            "/views/",
            "/templates/",
            "/presenters/",
            "/presentation/",
        ),
    ),
)


def _classify_layer(path: str) -> str | None:
    """Map a file path to a layer bucket or None if unmatched.

    Matching is case-insensitive on a slash-normalised copy of the path.
    Order in ``_LAYER_PATTERNS`` determines precedence on overlap.
    """
    if not path:
        return None
    norm = "/" + path.replace("\\", "/").lower().lstrip("/") + "/"
    for layer, fragments in _LAYER_PATTERNS:
        for frag in fragments:
            if frag in norm:
                return layer
    return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


_CALLER_SYMBOL_QUERY = """
    SELECT
        s.id AS sym_id,
        s.name AS sym_name,
        s.kind AS sym_kind,
        s.line_start AS sym_line,
        f.path AS sym_path
    FROM symbols s
    JOIN files f ON s.file_id = f.id
    WHERE s.kind IN ('function', 'method')
"""


def _load_callable_symbols(
    conn: sqlite3.Connection,
) -> list[tuple[int, str, str, int | None, str]]:
    """Pull every function/method symbol with its owning file path.

    Returns a list of ``(sym_id, sym_name, sym_kind, sym_line, sym_path)``
    tuples. Empty list on a connection that has no ``symbols`` table.
    """
    try:
        rows = conn.execute(_CALLER_SYMBOL_QUERY).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[tuple[int, str, str, int | None, str]] = []
    for r in rows:
        try:
            sid = r["sym_id"] if hasattr(r, "keys") else r[0]
            name = r["sym_name"] if hasattr(r, "keys") else r[1]
            kind = r["sym_kind"] if hasattr(r, "keys") else r[2]
            line = r["sym_line"] if hasattr(r, "keys") else r[3]
            path = r["sym_path"] if hasattr(r, "keys") else r[4]
        except (KeyError, IndexError, TypeError):
            continue
        if sid is None or not name or not path:
            continue
        out.append((int(sid), str(name), str(kind), line, str(path)))
    return out


def _load_callee_name_sets(
    conn: sqlite3.Connection,
    source_ids: set[int],
) -> dict[int, set[str]]:
    """Build ``{source_sym_id: {callee_name, ...}}`` for the given sources.

    Multiset collapse is intentional: we want set-Jaccard, not multiset
    Jaccard. Two callers that both call ``apply_tax`` once and ``apply_tax``
    twice still share the same domain primitive — counting that twice
    would over-weight chatty controllers.

    Empty mapping when no caller has any outbound call edges.
    """
    if not source_ids:
        return {}
    placeholders = ", ".join("?" for _ in CALL_EDGE_KINDS)
    # Pull edges in chunks to stay under SQLite's default ~999 host-param cap.
    out: dict[int, set[str]] = defaultdict(set)
    source_list = list(source_ids)
    chunk_size = 400
    for start in range(0, len(source_list), chunk_size):
        chunk = source_list[start : start + chunk_size]
        src_ph = ", ".join("?" for _ in chunk)
        sql = (
            f"SELECT e.source_id AS src, s.name AS callee_name "
            f"FROM edges e "
            f"JOIN symbols s ON e.target_id = s.id "
            f"WHERE e.kind IN ({placeholders}) "
            f"AND e.source_id IN ({src_ph})"
        )
        params: list = list(CALL_EDGE_KINDS) + chunk
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return {}
        for r in rows:
            try:
                src = r["src"] if hasattr(r, "keys") else r[0]
                callee = r["callee_name"] if hasattr(r, "keys") else r[1]
            except (KeyError, IndexError, TypeError):
                continue
            if src is None or not callee:
                continue
            out[int(src)].add(str(callee))
    return dict(out)


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two sets. 0.0 when both empty (degenerate case)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _layer_pair_key(layer_a: str, layer_b: str) -> tuple[str, str]:
    """Canonical-order tuple so (a,b) and (b,a) collapse to one bucket."""
    return (layer_a, layer_b) if layer_a <= layer_b else (layer_b, layer_a)


# ---------------------------------------------------------------------------
# Finding shape
# ---------------------------------------------------------------------------


def _make_finding(
    *,
    layer_a: str,
    layer_b: str,
    sym_a_name: str,
    sym_b_name: str,
    file_a: str,
    file_b: str,
    line_a: int | None,
    jaccard: float,
    shared_callees: list[str],
    total_unique_callees: int,
    threshold: float,
) -> dict:
    """Build the canonical finding dict for one cross-layer pair.

    W923: detector-specific wrapper; delegates the 11-key dict
    construction to the canonical ``make_smell_finding`` helper.
    Hardcoded smell_id/severity/kind/confidence + composed
    symbol_name / description / evidence dict remain detector-local.
    """
    description = (
        f"Cross-layer clone: {layer_a} {sym_a_name} and {layer_b} {sym_b_name} "
        f"share {len(shared_callees)}/{total_unique_callees} callees "
        f"(Jaccard {jaccard:.2f}). Likely duplicating the same domain callees."
    )
    evidence = {
        "shared_callees": sorted(shared_callees),
        "layer_a": layer_a,
        "layer_b": layer_b,
        "file_a": file_a,
        "file_b": file_b,
        "jaccard": round(jaccard, 4),
        "jaccard_threshold": threshold,
        "shared_callee_count": len(shared_callees),
        "total_unique_callees": total_unique_callees,
    }
    return make_smell_finding(
        "cross-layer-clone",
        "warning",
        f"{layer_a}:{sym_a_name} || {layer_b}:{sym_b_name}",
        "cross_layer_clone",
        _loc(file_a, line_a),
        round(jaccard, 4),
        threshold,
        description,
        evidence=evidence,
        confidence="structural",
        detector_version=CROSS_LAYER_CLONE_DETECTOR_VERSION,
    )


def _make_truncation_finding(
    *,
    layer_a: str,
    layer_b: str,
    rows_a: int,
    rows_b: int,
    budget: int,
) -> dict:
    """Build a sentinel finding disclosing a per-layer-pair budget truncation.

    Emitted when one ``(layer_a, layer_b)`` comparison would exceed
    ``budget`` (``rows_a * rows_b > budget``). The cross-layer scan stops
    enumerating that layer pair partway through; this finding makes the
    incompleteness LOUD so consumers never read the result as an
    exhaustive scan (Pattern-1 "structured signal lost" / "make fallback
    chains loud" discipline). ``kind`` is the dedicated
    ``cross_layer_clone_truncated`` so callers can filter sentinels from
    real clone findings; ``severity`` is ``info`` (it is a coverage caveat,
    not a code smell). The description ends on the LAW-4 concrete-noun
    terminal ``callers``.
    """
    description = (
        f"Cross-layer scan truncated: the {layer_a} x {layer_b} layer pair has "
        f"{rows_a} x {rows_b} callers, exceeding the {budget} per-pair comparison "
        f"budget. Some cross-layer clones in this pair may be unreported — raise "
        f"ROAM_CROSS_LAYER_PAIR_BUDGET to scan all {layer_a}/{layer_b} callers."
    )
    evidence = {
        "truncated": True,
        "layer_a": layer_a,
        "layer_b": layer_b,
        "rows_a": rows_a,
        "rows_b": rows_b,
        "pair_budget": budget,
        "would_be_comparisons": rows_a * rows_b,
    }
    return make_smell_finding(
        "cross-layer-clone",
        "info",
        f"{layer_a} x {layer_b} (budget truncated)",
        "cross_layer_clone_truncated",
        f"{layer_a} x {layer_b}",
        rows_a * rows_b,
        budget,
        description,
        evidence=evidence,
        confidence="structural",
        detector_version=CROSS_LAYER_CLONE_DETECTOR_VERSION,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _bucket_symbols_by_layer(
    symbols: list[tuple[int, str, str, int | None, str]],
) -> dict[str, list[tuple[int, str, str, int | None, str]]]:
    """Bucket callable symbols by classified layer; drop unmatched paths.

    Anything that doesn't map is dropped — this detector is intentionally
    focused on the layered-app pattern; arbitrary cross-module duplication
    is W95/W855 territory.
    """
    by_layer: dict[str, list[tuple[int, str, str, int | None, str]]] = defaultdict(list)
    for row in symbols:
        sym_id, sym_name, sym_kind, sym_line, sym_path = row
        layer = _classify_layer(sym_path)
        if layer is None:
            continue
        by_layer[layer].append(row)
    return by_layer


def _attach_callees_to_layers(
    by_layer: dict[str, list[tuple[int, str, str, int | None, str]]],
    callees_by_id: dict[int, set[str]],
) -> dict[str, list[tuple[int, str, str, int | None, str, set[str]]]]:
    """Re-bucket each layer keeping only symbols with non-empty callee sets.

    The comparison loop skips empties anyway but pre-filtering keeps the
    O(n*m) tighter.
    """
    layered_with_callees: dict[str, list[tuple[int, str, str, int | None, str, set[str]]]] = defaultdict(list)
    for layer, rows in by_layer.items():
        for sym_id, sym_name, sym_kind, sym_line, sym_path in rows:
            callees = callees_by_id.get(sym_id)
            if not callees:
                continue
            layered_with_callees[layer].append((sym_id, sym_name, sym_kind, sym_line, sym_path, callees))
    return layered_with_callees


def _compare_caller_pair(
    *,
    sid_a: int,
    name_a: str,
    line_a: int | None,
    path_a: str,
    callees_a: set[str],
    sid_b: int,
    name_b: str,
    path_b: str,
    callees_b: set[str],
    layer_a: str,
    layer_b: str,
    jaccard_threshold: float,
    min_shared_callees: int,
    seen_pairs: set[tuple[int, int]],
) -> dict | None:
    """Score one caller pair; return a finding dict or None when filtered out.

    Skips self-pairs, already-seen pairs, pairs below ``min_shared_callees``
    or below ``jaccard_threshold``. Mutates ``seen_pairs`` on accept.
    """
    # Skip same-symbol self-pairs (cannot happen across different layers
    # but guards against degenerate rows with mis-classified paths).
    if sid_a == sid_b:
        return None
    pair_key = (sid_a, sid_b) if sid_a < sid_b else (sid_b, sid_a)
    if pair_key in seen_pairs:
        return None
    shared = callees_a & callees_b
    if len(shared) < min_shared_callees:
        return None
    union = callees_a | callees_b
    if not union:
        return None
    jaccard = len(shared) / len(union)
    if jaccard < jaccard_threshold:
        return None
    seen_pairs.add(pair_key)
    return _make_finding(
        layer_a=layer_a,
        layer_b=layer_b,
        sym_a_name=name_a,
        sym_b_name=name_b,
        file_a=path_a,
        file_b=path_b,
        line_a=line_a,
        jaccard=jaccard,
        shared_callees=sorted(shared),
        total_unique_callees=len(union),
        threshold=jaccard_threshold,
    )


def _scan_layer_pair(
    *,
    layer_a: str,
    layer_b: str,
    rows_a: list[tuple[int, str, str, int | None, str, set[str]]],
    rows_b: list[tuple[int, str, str, int | None, str, set[str]]],
    pair_budget: int,
    jaccard_threshold: float,
    min_shared_callees: int,
    seen_pairs: set[tuple[int, int]],
) -> tuple[list[dict], bool]:
    """Scan all caller pairs across two layers; return (findings, truncated).

    Truncate when ``len(rows_a) * len(rows_b) > pair_budget`` — we still
    scan as much of layer A as the budget allows, then the caller emits a
    sentinel finding so the truncation is disclosed rather than silent.
    """
    full_comparisons = len(rows_a) * len(rows_b)
    truncated = full_comparisons > pair_budget
    findings: list[dict] = []
    comparisons_done = 0
    for sid_a, name_a, _ka, line_a, path_a, callees_a in rows_a:
        if truncated and comparisons_done >= pair_budget:
            break
        for sid_b, name_b, _kb, _lb, path_b, callees_b in rows_b:
            comparisons_done += 1
            finding = _compare_caller_pair(
                sid_a=sid_a,
                name_a=name_a,
                line_a=line_a,
                path_a=path_a,
                callees_a=callees_a,
                sid_b=sid_b,
                name_b=name_b,
                path_b=path_b,
                callees_b=callees_b,
                layer_a=layer_a,
                layer_b=layer_b,
                jaccard_threshold=jaccard_threshold,
                min_shared_callees=min_shared_callees,
                seen_pairs=seen_pairs,
            )
            if finding is not None:
                findings.append(finding)
    return findings, truncated


def _finalize_findings(
    findings: list[dict],
    truncation_findings: list[dict],
) -> list[dict]:
    """Deterministic merge: real findings by ``-metric_value``/name, then sentinels.

    Sentinel truncation findings (if any) ride at the END of the list,
    ordered by layer pair, so they never perturb the ordering of real
    clone findings — a below-budget scan produces zero of these and the
    output is byte-identical to the pre-budget detector.
    """
    findings.sort(key=lambda f: (-f["metric_value"], f["symbol_name"]))
    truncation_findings.sort(key=lambda f: f["symbol_name"])
    return findings + truncation_findings


def detect_cross_layer_clones(
    conn: sqlite3.Connection,
    *,
    jaccard_threshold: float = 0.7,
    min_shared_callees: int = 3,
) -> list[dict]:
    """Detect cross-architectural-layer duplication (W856).

    Parameters
    ----------
    conn:
        SQLite connection over a populated roam DB. ``row_factory`` should
        be ``sqlite3.Row`` for the cleanest column-name access pattern;
        plain tuple-row connections work too.
    jaccard_threshold:
        Minimum Jaccard similarity of two callers' callee-name sets to
        flag the pair. Default ``0.7``.
    min_shared_callees:
        Minimum intersection size for a pair to count. Default ``3``;
        below this the signal is dominated by tiny-stub coincidence
        (correct delegation, not duplication).

    Returns
    -------
    list[dict]
        Findings in the canonical ``_finding``-shaped layout. Sorted by
        descending Jaccard then by ``symbol_name`` for determinism.
        Empty list on a DB with no callable symbols, no call edges, or
        no pair clearing the threshold — never raises on an empty
        corpus.
    """
    symbols = _load_callable_symbols(conn)
    if not symbols:
        return []

    by_layer = _bucket_symbols_by_layer(symbols)
    if len(by_layer) < 2:
        # Need at least two layers populated to have any cross-layer pair.
        return []

    # Pull callee name sets for every symbol that landed in a layer
    # bucket. Symbols with no outbound calls are dropped here — they
    # cannot be a clone target by definition.
    all_layered_ids: set[int] = set()
    for rows in by_layer.values():
        for r in rows:
            all_layered_ids.add(r[0])
    callees_by_id = _load_callee_name_sets(conn, all_layered_ids)

    layered_with_callees = _attach_callees_to_layers(by_layer, callees_by_id)
    if len(layered_with_callees) < 2:
        return []

    layers = sorted(layered_with_callees.keys())
    seen_pairs: set[tuple[int, int]] = set()
    findings: list[dict] = []
    truncation_findings: list[dict] = []

    # Defensive per-layer-pair comparison budget — bounds the O(|A|*|B|)
    # double loop on huge LAYERED repos. Below the budget the loop runs
    # exactly as before, so output stays byte-identical on any repo whose
    # layer pairs fit. roam-code is not layered, so this never engages here.
    pair_budget = _cross_layer_pair_budget()

    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            layer_a, layer_b = layers[i], layers[j]
            rows_a = layered_with_callees[layer_a]
            rows_b = layered_with_callees[layer_b]
            pair_findings, truncated = _scan_layer_pair(
                layer_a=layer_a,
                layer_b=layer_b,
                rows_a=rows_a,
                rows_b=rows_b,
                pair_budget=pair_budget,
                jaccard_threshold=jaccard_threshold,
                min_shared_callees=min_shared_callees,
                seen_pairs=seen_pairs,
            )
            if truncated:
                truncation_findings.append(
                    _make_truncation_finding(
                        layer_a=layer_a,
                        layer_b=layer_b,
                        rows_a=len(rows_a),
                        rows_b=len(rows_b),
                        budget=pair_budget,
                    )
                )
            findings.extend(pair_findings)

    return _finalize_findings(findings, truncation_findings)


__all__ = [
    "CROSS_LAYER_CLONE_DETECTOR",
    "CROSS_LAYER_CLONE_DETECTOR_VERSION",
    "detect_cross_layer_clones",
]
