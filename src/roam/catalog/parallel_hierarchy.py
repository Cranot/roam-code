"""Fowler "Parallel Inheritance Hierarchies" smell detector (W857).

When every subclass of class ``A`` has a mirror subclass of class ``B``
(e.g. ``EmployeeUS`` / ``EmployeeUK`` paired with ``EmployeeUSPayroll`` /
``EmployeeUKPayroll``), the two hierarchies are likely tracking the same
domain axis. Fowler's prescription is to extract that axis: collapse the
mirrored pair via Strategy / Bridge / composition so a single hierarchy
varies along one axis.

Why a new module (not in ``smells.py``)
---------------------------------------
``smells.py`` is the canonical home for Fowler-family detectors, but it is
already 1000+ lines and frequently touched. W857 lands the detector as
its own module so the new detector can mature behind a stable surface
without colliding with in-flight session state on ``smells.py``.

Algorithm
---------
1. Pull ``(superclass_id, subclass_id)`` pairs from ``edges`` rows whose
   kind is one of the inheritance kinds (``'inherits'`` / ``'extends'``).
2. Group by superclass; keep superclasses with >= 2 subclasses.
3. For every ordered pair ``(A, B)`` of remaining superclasses
   (``A.id < B.id`` to avoid double-counting), compute the Jaccard
   similarity of the tokenised subclass-name sets.
4. When the similarity meets ``jaccard_threshold`` (default ``0.7``),
   emit a finding tagged with the matching subclass markers.
5. Co-change corroboration is OPTIONAL — surfaced via
   ``evidence.cochange_confirmed`` (``None`` when the corroboration
   step has not run; the pure-Jaccard signal still ships).

Confidence tier: ``structural`` — we read AST-derived ``inherits`` /
``extends`` edges from the graph; the heuristic is the token-set Jaccard
on top.

Findings shape
--------------
Matches the dict layout used by ``roam.catalog.smells._finding`` so the
``smells`` command can absorb this detector unchanged once it is wired
in. Each finding additionally carries an ``evidence`` dict for the
findings-registry-level payload.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional

from roam.catalog._shared import make_smell_finding
from roam.db.edge_kinds import INHERITANCE_EDGE_KINDS as _CANONICAL_INHERITANCE_EDGE_KINDS

# Closed enum of edge-kind values that represent class inheritance.
#
# W543-followup: plugin-defensive widening over the canonical set
# (:data:`roam.db.edge_kinds.INHERITANCE_EDGE_KINDS` = ``("inherits",
# "implements", "uses_trait")``). The canonical set is sourced from
# the shared module so a future writer addition lands here for free.
#
# The extra ``'extends'`` literal is a *deliberate widening*, NOT a
# canonical kind: no in-tree writer emits it (verified by the W543
# drift-test in ``tests/test_w543_edge_kind_canonical.py``).
# ``tests/test_w857_parallel_hierarchy.py::test_extends_edge_kind_also_recognized``
# pins this detector to accept ``kind='extends'`` rows so plugin
# extractors that diverge from the canonical writer convention
# (see ``languages/extractor_schema.py:InheritancePattern.relationship``
# default value ``"extends"``) still produce findings here. Removing
# ``'extends'`` would break that contract — keep the widening local.
INHERITANCE_EDGE_KINDS: tuple[str, ...] = _CANONICAL_INHERITANCE_EDGE_KINDS + ("extends",)

# Detector identity constants (parallel the convention used by other
# catalog detectors — version stamp lets findings-registry consumers
# spot rows produced under a stale shape).
PARALLEL_HIERARCHY_DETECTOR = "parallel-hierarchy"
PARALLEL_HIERARCHY_DETECTOR_VERSION = "1.0.0"

_CAMEL_SPLIT_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+",
)


def _tokenize(name: str) -> set[str]:
    """Split ``camelCase`` / ``snake_case`` / ``PascalCase`` into lowercase tokens.

    Examples
    --------
    >>> sorted(_tokenize("EmployeeUSPayroll"))
    ['employee', 'payroll', 'us']
    >>> sorted(_tokenize("savings_account_v2"))
    ['2', 'account', 'savings', 'v']
    """
    if not name:
        return set()
    # snake_case → space; let the camel regex pick up the rest.
    cleaned = name.replace("_", " ").replace("-", " ")
    tokens: set[str] = set()
    for chunk in cleaned.split():
        for m in _CAMEL_SPLIT_RE.finditer(chunk):
            tok = m.group(0).lower()
            if tok:
                tokens.add(tok)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _strip_super_token_overlap(sub_tokens: set[str], super_tokens: set[str]) -> set[str]:
    """Remove tokens that come from the superclass name itself.

    The Jaccard signal we want is on the *variant* portion of subclass
    names (``US`` / ``UK`` / ``Payroll`` / ``Savings``), not on the
    shared root (``Employee`` / ``Account``). Without this step,
    ``EmployeeUS`` and ``AccountUS`` share ``us`` only — but because the
    set sizes are dominated by the shared root, the Jaccard signal gets
    washed out. Stripping the parent's tokens makes the marker
    comparison crisp.
    """
    return sub_tokens - super_tokens


_SUBCLASS_QUERY = """
    SELECT
        e.target_id AS super_id,
        s_super.name AS super_name,
        s_super.kind AS super_kind,
        e.source_id AS sub_id,
        s_sub.name AS sub_name,
        s_sub.kind AS sub_kind,
        f_sub.path AS sub_path,
        s_sub.line_start AS sub_line
    FROM edges e
    JOIN symbols s_super ON e.target_id = s_super.id
    JOIN symbols s_sub ON e.source_id = s_sub.id
    LEFT JOIN files f_sub ON s_sub.file_id = f_sub.id
    WHERE e.kind IN ({placeholders})
"""


def _load_inheritance_pairs(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in INHERITANCE_EDGE_KINDS)
    sql = _SUBCLASS_QUERY.format(placeholders=placeholders)
    try:
        return conn.execute(sql, INHERITANCE_EDGE_KINDS).fetchall()
    except sqlite3.OperationalError:
        return []


def _finding(
    super_a_name: str,
    super_b_name: str,
    location: str,
    metric_value: float,
    threshold: float,
    description: str,
    evidence: dict,
) -> dict:
    # W923: detector-specific wrapper; delegates the 11-key dict
    # construction to the canonical ``make_smell_finding`` helper.
    # Hardcoded smell_id/severity/kind/confidence + composed
    # symbol_name + rounded metric_value remain detector-local.
    return make_smell_finding(
        "parallel-hierarchy",
        "warning",
        f"{super_a_name} || {super_b_name}",
        "class_pair",
        location,
        round(metric_value, 4),
        threshold,
        description,
        evidence=evidence,
        confidence="structural",
        detector_version=PARALLEL_HIERARCHY_DETECTOR_VERSION,
    )


def _markers_and_union(
    subs: list[tuple[int, str, Optional[str], Optional[int]]],
    super_name: str,
) -> tuple[list[tuple[str, set[str]]], set[str]]:
    """Build the per-subclass marker sets + their union for one hierarchy.

    Each subclass name is tokenised; tokens that overlap with the
    superclass name are stripped so the comparison signal lives on the
    *variant* portion (``US``/``UK``/``Payroll``) rather than the shared
    root (``Employee``). Subclasses with no remaining markers are
    dropped — they can't contribute to the Jaccard signal.

    The A-side and B-side inner loops in ``detect_parallel_hierarchy``
    are byte-identical apart from variable suffixes; this helper is the
    extracted common form.
    """
    super_toks = _tokenize(super_name)
    markers: list[tuple[str, set[str]]] = []
    for _sid, name, _p, _l in subs:
        toks = _strip_super_token_overlap(_tokenize(name), super_toks)
        if toks:
            markers.append((name, toks))
    union: set[str] = set()
    for _n, toks in markers:
        union |= toks
    return markers, union


def detect_parallel_hierarchy(
    conn: sqlite3.Connection,
    *,
    jaccard_threshold: float = 0.7,
    min_subclasses: int = 2,
) -> list[dict]:
    """Detect Fowler's "Parallel Inheritance Hierarchies" smell.

    Parameters
    ----------
    conn:
        SQLite connection (``row_factory`` should be ``sqlite3.Row`` so
        the column-name access pattern works; gracefully handles plain
        connections too).
    jaccard_threshold:
        Minimum Jaccard similarity of marker token sets to flag a
        hierarchy pair as parallel. Default ``0.7``.
    min_subclasses:
        Minimum number of subclasses each side must have. Default ``2``
        (a single subclass per superclass cannot exhibit the parallel
        pattern; it is just point inheritance).

    Returns
    -------
    list[dict]
        Findings, one per parallel hierarchy pair, with ``smell_id ==
        'parallel-hierarchy'``. Empty list when the DB has no
        inheritance edges or no pair clears the threshold.
    """
    rows = _load_inheritance_pairs(conn)
    if not rows:
        return []

    # Group: super_id -> (super_name, [(sub_id, sub_name, sub_path, sub_line), ...])
    grouped: dict[int, tuple[str, list[tuple[int, str, Optional[str], Optional[int]]]]] = {}
    for r in rows:
        try:
            super_id = int(r["super_id"]) if r["super_id"] is not None else None
            super_name = r["super_name"]
            sub_id = int(r["sub_id"]) if r["sub_id"] is not None else None
            sub_name = r["sub_name"]
            sub_path = r["sub_path"] if "sub_path" in r.keys() else None
            sub_line = r["sub_line"] if "sub_line" in r.keys() else None
        except (KeyError, IndexError, TypeError):
            continue
        if super_id is None or sub_id is None or not super_name or not sub_name:
            continue
        bucket = grouped.setdefault(super_id, (super_name, []))
        bucket[1].append((sub_id, sub_name, sub_path, sub_line))

    # Keep superclasses with >= min_subclasses distinct subclass ids.
    eligible: list[tuple[int, str, list[tuple[int, str, Optional[str], Optional[int]]]]] = []
    for super_id, (super_name, subs) in grouped.items():
        # Deduplicate on sub_id.
        seen: set[int] = set()
        unique_subs: list[tuple[int, str, Optional[str], Optional[int]]] = []
        for s in subs:
            if s[0] in seen:
                continue
            seen.add(s[0])
            unique_subs.append(s)
        if len(unique_subs) >= min_subclasses:
            eligible.append((super_id, super_name, unique_subs))

    if len(eligible) < 2:
        return []

    findings: list[dict] = []
    # Iterate ordered pairs (A.id < B.id) to avoid double-counting.
    eligible.sort(key=lambda t: t[0])
    for i in range(len(eligible)):
        super_a_id, super_a_name, subs_a = eligible[i]
        markers_a, union_a = _markers_and_union(subs_a, super_a_name)
        if len(markers_a) < min_subclasses or not union_a:
            continue

        for j in range(i + 1, len(eligible)):
            super_b_id, super_b_name, subs_b = eligible[j]
            markers_b, union_b = _markers_and_union(subs_b, super_b_name)
            if len(markers_b) < min_subclasses or not union_b:
                continue

            similarity = _jaccard(union_a, union_b)
            if similarity < jaccard_threshold:
                continue

            # Build the matched marker pairs — every A-side subclass that
            # shares >=1 marker with at least one B-side subclass.
            matched_pairs: list[tuple[str, str, list[str]]] = []
            for a_name, a_toks in markers_a:
                for b_name, b_toks in markers_b:
                    shared = sorted(a_toks & b_toks)
                    if shared:
                        matched_pairs.append((a_name, b_name, shared))
            # Need at least min_subclasses paired markers for this to be
            # a genuine parallel hierarchy (not a one-off token collision).
            distinct_a = {p[0] for p in matched_pairs}
            distinct_b = {p[1] for p in matched_pairs}
            if len(distinct_a) < min_subclasses or len(distinct_b) < min_subclasses:
                continue

            # Pick a location: first matched subclass on the A side.
            location = "<unknown>"
            for _sid, name, path, line in subs_a:
                if name in distinct_a and path:
                    location = f"{path}:{line}" if line else path
                    break

            description = (
                f"Parallel hierarchies: {super_a_name} subclasses "
                f"{sorted(distinct_a)} mirror {super_b_name} subclasses "
                f"{sorted(distinct_b)} (jaccard={similarity:.2f}). "
                f"Consider extracting the varying axis via Strategy or Bridge."
            )

            evidence = {
                "super_a": {"id": super_a_id, "name": super_a_name},
                "super_b": {"id": super_b_id, "name": super_b_name},
                "subclasses_a": sorted(distinct_a),
                "subclasses_b": sorted(distinct_b),
                "shared_markers": sorted(union_a & union_b),
                "jaccard": round(similarity, 4),
                "jaccard_threshold": jaccard_threshold,
                "cochange_confirmed": None,  # corroboration step deferred
                "matched_pairs": [{"a": a, "b": b, "shared": sh} for (a, b, sh) in matched_pairs],
            }

            findings.append(
                _finding(
                    super_a_name=super_a_name,
                    super_b_name=super_b_name,
                    location=location,
                    metric_value=similarity,
                    threshold=jaccard_threshold,
                    description=description,
                    evidence=evidence,
                )
            )

    return findings


__all__ = [
    "INHERITANCE_EDGE_KINDS",
    "PARALLEL_HIERARCHY_DETECTOR",
    "PARALLEL_HIERARCHY_DETECTOR_VERSION",
    "detect_parallel_hierarchy",
]
