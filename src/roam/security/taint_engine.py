"""roam taint — graph-reach taint with OpenVEX justifications.

A YAML-rule-driven graph-reach BFS over the existing edges table with
sanitizer-stop nodes. Produces SARIF + OpenVEX-grade attestation
evidence; deliberately simpler than year-long abstract-interpretation
approaches like CodeQL.

Public API:

* :func:`load_rules` — parse a YAML rule pack into :class:`TaintRule`
  objects.
* :func:`run_taint` — reach-analysis from rule sources → sinks.
* :func:`vex_justification_for` — map a finding's reach status to one
  of the five legal OpenVEX justification strings.

OpenVEX correctness: ``code_not_reachable`` is **not** in the spec —
we never emit it. The legal strings are
``component_not_present``, ``vulnerable_code_not_present``,
``vulnerable_code_not_in_execute_path``,
``vulnerable_code_cannot_be_controlled_by_adversary``,
``inline_mitigations_already_exist``.
"""

from __future__ import annotations

import re
import sqlite3
import warnings
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from roam.commands._yaml_loader import load_yaml_with_warnings
from roam.db.connection import batched_in
from roam.db.edge_kinds import call_or_ref_in_clause
from roam.index.relations import _mask_strings_and_comments, _read_source_text
from roam.output._severity import validate_severity

__all__ = [
    "OPENVEX_JUSTIFICATIONS",
    "OPENVEX_STATUSES",
    "TaintFinding",
    "TaintRule",
    "load_rules",
    "run_taint",
    "vex_justification_for",
]

# OpenVEX justification strings — verbatim from the spec. NEVER add
# anything here that the spec doesn't list. Sorted set for stable test
# assertions.
OPENVEX_JUSTIFICATIONS: frozenset[str] = frozenset(
    {
        "component_not_present",
        "vulnerable_code_not_present",
        "vulnerable_code_not_in_execute_path",
        "vulnerable_code_cannot_be_controlled_by_adversary",
        "inline_mitigations_already_exist",
    }
)

# OpenVEX status values — also verbatim spec. NB: ``fixed`` is a status,
# not a justification.
OPENVEX_STATUSES: frozenset[str] = frozenset({"not_affected", "affected", "fixed", "under_investigation"})


@dataclass
class TaintRule:
    """A single source → sink → (optional) sanitizer triplet."""

    rule_id: str
    description: str
    severity: str = "warning"  # 'error', 'warning', 'note'
    cwe: str = ""
    languages: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    sinks: tuple[str, ...] = ()
    sanitizers: tuple[str, ...] = ()
    # W454: when True, only qualified-name matches count — bare-name
    # matches are skipped. Reduces FPs on sinks like
    # ``render_template_string`` / ``executeQuery`` that get reused as
    # method names on user-defined wrappers. Default False preserves the
    # legacy permissive match.
    qualified_only: bool = False
    # W492: OWASP Top 10 (2021) category tag, e.g. ``"A03:2021_Injection"``
    # or ``"A08:2021_Software_and_Data_Integrity_Failures"``. Empty when
    # the rule YAML did not declare one. Loaded verbatim — we don't
    # validate the spelling here because new OWASP revisions ship every
    # 3-4 years and rule authors should be able to stamp the new keyword
    # without a code change. Surfaced through findings registry
    # ``evidence_json`` (W492) and SARIF ``result.properties.tags[]``
    # (W453) so downstream consumers (GitHub Code Scanning, audit
    # exports, governance reports) can filter / aggregate by OWASP
    # category.
    owasp_top10: str = ""


@dataclass
class TaintFinding:
    """One reach result — a source that can reach a sink without being
    sanitized along the way (or a sanitized one, kept as evidence for
    VEX ``inline_mitigations_already_exist``)."""

    rule_id: str
    severity: str
    cwe: str
    source_symbol: dict  # {id, name, file, line}
    sink_symbol: dict
    path_symbols: list[dict]  # ordered hops from source to sink
    sanitizer_in_path: bool
    # True when the BFS exited via the max_hops or per-node fan-out cap
    # rather than exhausting the graph. The "no path" return value of
    # the search engine cannot distinguish "definitely not reachable"
    # from "search hit a cap" without this flag — and downstream OpenVEX
    # consumers need to know so they can map to ``under_investigation``
    # rather than ``vulnerable_code_not_in_execute_path``.
    path_truncated: bool = False
    # W492: OWASP Top 10 category copied from the originating rule. Kept
    # on the finding so downstream consumers (findings registry emit,
    # SARIF taint_to_sarif) don't have to re-resolve the rule. Empty
    # when the rule did not declare an owasp_top10 mapping.
    owasp_top10: str = ""


# ---------------------------------------------------------------------------
# Rule loading (zero-dep YAML subset via shared YAML loader)
# ---------------------------------------------------------------------------


def load_rules(rules_dir: Path | str) -> list[TaintRule]:
    """Load every ``*.yaml`` file under *rules_dir* as a TaintRule.

    Uses the shared YAML file loader for I/O + malformed-file handling,
    with the taint-specific subset parser as the sole parser so invalid
    taint keys still get rejected consistently. Files that fail to parse
    are skipped rather than crashing the whole load — one bad rule
    shouldn't take out the rest.
    """
    rules_path = Path(rules_dir)
    if not rules_path.is_dir():
        return []

    out: list[TaintRule] = []
    for yaml_file in sorted(rules_path.glob("*.yaml")):
        doc, status = load_yaml_with_warnings(
            yaml_file,
            tiny_parser=_parse_yaml_subset,
            config_label="taint-rules",
            force_tiny_parser=True,
            return_status=True,
        )
        if status in {"parse_error", "read_error", "wrong_root_type", "schema_invalid"}:
            continue
        if not isinstance(doc, dict):
            continue
        rule_id = str(doc.get("id") or yaml_file.stem)
        sources = tuple(doc.get("sources") or ())
        sinks = tuple(doc.get("sinks") or ())
        sanitizers = tuple(doc.get("sanitizers") or ())
        qualified_only = _coerce_bool(doc.get("qualified_only"), default=False)
        # W479: under qualified_only=true, bare (dot-less) entries in
        # sources/sinks/sanitizers are silent no-ops (see W454/W467
        # tightening in _symbols_matching). Warn at load time so a rule
        # author can either qualify the entry or drop qualified_only
        # rather than silently shipping with reduced recall.
        if qualified_only:
            _warn_bare_entries_under_qualified_only(
                rule_id,
                sources=sources,
                sinks=sinks,
                sanitizers=sanitizers,
            )
        # W548: closed-enum validation at YAML load. validate_severity()
        # warns the rule author when their YAML spelling is non-canonical
        # (e.g. "HIGH" or "moderate") and returns the canonical form. Pre-
        # W548 these silently passed through verbatim and produced
        # downstream SARIF-level mismatches.
        raw_sev = doc.get("severity")
        canonical_sev = validate_severity(raw_sev, source=rule_id) if raw_sev else "warning"
        out.append(
            TaintRule(
                rule_id=rule_id,
                description=str(doc.get("description") or ""),
                severity=canonical_sev,
                cwe=str(doc.get("cwe") or ""),
                languages=tuple(doc.get("languages") or ()),
                sources=sources,
                sinks=sinks,
                sanitizers=sanitizers,
                qualified_only=qualified_only,
                owasp_top10=str(doc.get("owasp_top10") or ""),
            )
        )
    return out


def _warn_bare_entries_under_qualified_only(
    rule_id: str,
    *,
    sources: Iterable[str],
    sinks: Iterable[str],
    sanitizers: Iterable[str],
) -> None:
    sections: tuple[tuple[str, Iterable[str]], ...] = (
        ("sources", sources),
        ("sinks", sinks),
        ("sanitizers", sanitizers),
    )
    for kind, entries in sections:
        for name in entries:
            name_text = str(name)
            if "." in name_text:
                continue
            warnings.warn(
                f"[taint-engine] rule {rule_id!r}: bare {kind[:-1]} "
                f"{name_text!r} is a no-op under qualified_only=true; "
                f"either qualify it or drop qualified_only",
                stacklevel=3,
            )


def _coerce_bool(value: object, *, default: bool) -> bool:
    """Coerce a YAML-subset scalar to bool. The subset parser returns
    everything as strings, so ``qualified_only: true`` arrives as the
    literal string ``"true"``. Accept the usual YAML truthy/falsy
    spellings; anything unrecognised falls back to *default* — keeping
    a typo from silently flipping security semantics."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "on", "1"}:
            return True
        if v in {"false", "no", "off", "0"}:
            return False
    return default


_VALID_KEY = __import__("re").compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


def _parse_yaml_subset(text: str) -> dict:
    """Parse the limited subset our taint rules use:

    * Top-level scalar keys (``id: foo``, ``severity: warning``)
    * Lists of strings via ``- value`` syntax
    * Inline lists ``[a, b]``
    * Comments starting with ``#``

    Keys must match ``[a-zA-Z_][a-zA-Z0-9_-]*`` — anything else is
    rejected so a malformed file like ``"not yaml :::"`` doesn't smuggle
    through. Bad rules should be skipped, not partially accepted.
    """
    out: dict = {}
    current_list: list | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  - ") or line.startswith("    - "):
            if current_list is None:
                raise ValueError(f"list item with no current key: {stripped!r}")
            current_list.append(stripped[1:].strip().strip('"').strip("'"))
            continue
        # Top-level "key: value" or "key:"
        if ":" not in stripped:
            raise ValueError(f"expected 'key:' line, got {stripped!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        if not _VALID_KEY.match(key):
            raise ValueError(f"invalid key: {key!r}")
        value = value.strip()
        if value:
            current_list = None
            v = value.strip('"').strip("'")
            # Handle inline list "[a, b, c]"
            if v.startswith("[") and v.endswith("]"):
                items = [s.strip().strip('"').strip("'") for s in v[1:-1].split(",")]
                items = [s for s in items if s]
                out[key] = items
            else:
                out[key] = v
        else:
            current_list = []
            out[key] = current_list
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _symbols_matching(
    conn: sqlite3.Connection,
    names: Iterable[str],
    languages: Iterable[str],
    *,
    qualified_only: bool = False,
) -> list[dict]:
    """Return symbols whose name OR qualified_name matches any of *names*.

    Match is exact-name OR ``%.<name>`` suffix (so ``request.args`` matches
    qualified-names like ``flask.request.args``). When *languages* is
    non-empty, only symbols whose file language is in the list are
    returned.

    When *qualified_only* is True (W454/W467), match becomes strict:

    1. The bare-name branch (``s.name = ?``) is skipped.
    2. The exact-match branch (``s.qualified_name = ?``) is ALSO
       skipped for any *name* that does not itself contain a dot,
       because a top-level user-defined ``def executeQuery`` has
       ``qualified_name = 'executeQuery'`` and would otherwise still
       fire as a FP.
    3. The suffix-LIKE branch (``s.qualified_name LIKE '%.<name>'``)
       is ALSO skipped for bare (dot-less) names — ``%.executeQuery``
       matches the user wrapper ``MyDao.executeQuery``, which is the
       exact FP this flag exists to suppress (W467 root cause).
    4. Dotted names (``Statement.executeQuery``,
       ``java.sql.Statement.executeQuery``) keep BOTH the exact and
       suffix-LIKE branches: ``java.sql.Statement.executeQuery``
       matches itself exactly, and ``Statement.executeQuery`` matches
       any qualified name ending in ``.Statement.executeQuery``.

    Net effect: under qualified_only=True, bare names in the rule's
    sink/source/sanitizer lists are NO-OPS (silently skipped). Rule
    authors must list import-qualified sinks (``java.sql.*`` /
    ``javax.servlet.*``) for matching to fire.

    Used to suppress FPs on sinks like ``render_template_string`` /
    ``executeQuery`` that get reused as method names on user-defined
    wrappers; sinks must be reached through their import-qualified
    path. Default False keeps backwards-compat with the permissive
    matcher.
    """
    name_list = list(names)
    if not name_list:
        return []

    or_clauses: list[str] = []
    params: list = []
    for name in name_list:
        if not qualified_only:
            or_clauses.append("s.name = ?")
            params.append(name)
            or_clauses.append("s.qualified_name = ?")
            params.append(name)
            or_clauses.append("s.qualified_name LIKE ?")
            params.append(f"%.{name}")
        else:
            # qualified_only=True: bare names are NO-OPS — they
            # would otherwise match top-level user defs via
            # qualified_name = bare_name AND match user wrappers via
            # %.<name> suffix. The rule must list dotted sinks.
            if "." not in name:
                continue
            or_clauses.append("s.qualified_name = ?")
            params.append(name)
            or_clauses.append("s.qualified_name LIKE ?")
            params.append(f"%.{name}")

    if not or_clauses:
        # Every name was a bare-name no-op under qualified_only=True.
        return []

    lang_clause = ""
    lang_list = list(languages)
    if lang_list:
        lang_clause = " AND f.language IN (" + ",".join("?" for _ in lang_list) + ")"
        params.extend(lang_list)

    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name, s.line_start, "
        "       f.path AS file_path, f.language "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        f"WHERE ({' OR '.join(or_clauses)}){lang_clause}",
        params,
    ).fetchall()

    return [
        {
            "id": int(r[0]),
            "name": r[1],
            "qualified_name": r[2],
            "line": r[3],
            "file": r[4],
        }
        for r in rows
    ]


_BFS_FAN_OUT_LIMIT = 200
_BfsQueueState = tuple[int, list[int], bool]


def _load_frontier_edges_with_bounded_round_trips(
    conn: sqlite3.Connection,
    source_ids: Iterable[int],
) -> tuple[dict[int, list[int]], bool]:
    """Batch a BFS frontier while preserving each node's fan-out bound."""
    source_list = list(source_ids)
    if not source_list:
        return {}, False

    rows = batched_in(
        conn,
        "SELECT source_id, target_id, total_edges "
        "FROM ("
        "    SELECT source_id, target_id, "
        "           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY id) AS edge_rank, "
        "           COUNT(*) OVER (PARTITION BY source_id) AS total_edges "
        "    FROM edges "
        f"    WHERE source_id IN ({{ph}}) AND {call_or_ref_in_clause()}"
        ") "
        "WHERE edge_rank <= ? "
        "ORDER BY source_id, edge_rank",
        source_list,
        post=(_BFS_FAN_OUT_LIMIT,),
    )

    by_source: dict[int, list[int]] = {}
    truncated = False
    for row in rows:
        source_id = int(row[0])
        by_source.setdefault(source_id, []).append(int(row[1]))
        if int(row[2]) >= _BFS_FAN_OUT_LIMIT:
            truncated = True
    return by_source, truncated


def _drain_frontier_for_batched_reachability(
    queue: deque[_BfsQueueState],
    goal_ids: set[int],
    start_ids: set[int],
    max_hops: int,
) -> tuple[list[_BfsQueueState], tuple[list[int], bool] | None, bool]:
    """Keep terminal and hop-cap checks outside the batched SQL step."""
    frontier: list[_BfsQueueState] = []
    truncated = False
    while queue:
        node, path, has_sanitizer = queue.popleft()
        if node in goal_ids and node not in start_ids:
            return frontier, (path, has_sanitizer), truncated
        if len(path) > max_hops:
            truncated = True
            continue
        frontier.append((node, path, has_sanitizer))
    return frontier, None, truncated


def _enqueue_targets_to_preserve_bfs_order(
    queue: deque[_BfsQueueState],
    frontier: list[_BfsQueueState],
    edges_by_source: dict[int, list[int]],
    visited: set[int],
    sanitizer_ids: set[int],
) -> None:
    """Apply batched edges in frontier order so path selection stays BFS."""
    for node, path, has_sanitizer in frontier:
        for tgt in edges_by_source.get(node, ()):
            if tgt in visited:
                continue
            visited.add(tgt)
            queue.append((tgt, path + [tgt], has_sanitizer or tgt in sanitizer_ids))


def _bfs_path(
    conn: sqlite3.Connection,
    start_ids: set[int],
    goal_ids: set[int],
    sanitizer_ids: set[int],
    *,
    max_hops: int = 6,
) -> tuple[list[int] | None, bool, bool]:
    """BFS over ``edges`` from any *start* to any *goal*.

    Returns a 3-tuple ``(path, has_sanitizer, truncated)``:

    * ``path``: list of symbol ids from source to sink, or ``None`` when
      no path exists within the bounds.
    * ``has_sanitizer``: True when a sanitizer node lay on the returned
      path (only meaningful when ``path`` is non-None).
    * ``truncated``: True when the search was bounded by ``max_hops`` or
      the per-node fan-out cap. When ``path is None and truncated``, the
      caller cannot conclude "definitely not reachable" — only "no path
      within the bounds." OpenVEX consumers must map this to
      ``under_investigation`` rather than
      ``vulnerable_code_not_in_execute_path``.
    """
    if not start_ids or not goal_ids:
        return None, False, False

    queue: deque[_BfsQueueState] = deque((s, [s], s in sanitizer_ids) for s in start_ids)
    visited: set[int] = set(start_ids)
    truncated = False

    while queue:
        frontier, found, hop_truncated = _drain_frontier_for_batched_reachability(
            queue,
            goal_ids,
            start_ids,
            max_hops,
        )
        if hop_truncated:
            truncated = True
        if found is not None:
            path, has_sanitizer = found
            return path, has_sanitizer, truncated
        if not frontier:
            continue

        edges_by_source, frontier_truncated = _load_frontier_edges_with_bounded_round_trips(
            conn,
            (node for node, _path, _has_sanitizer in frontier),
        )
        if frontier_truncated:
            # Hit the per-node fan-out cap — the path may exist beyond
            # the truncated edge set. Mark and proceed (don't propagate
            # within this branch — other branches may still find a path).
            truncated = True
        _enqueue_targets_to_preserve_bfs_order(queue, frontier, edges_by_source, visited, sanitizer_ids)

    return None, False, truncated


def _intraprocedural_co_calls(
    conn: sqlite3.Connection,
    source_ids: set[int],
    sink_ids: set[int],
    sanitizer_ids: set[int],
) -> list[tuple[int, int, int, bool]]:
    """Find functions that call BOTH a taint source and a sink.

    Catches the ``y = source(); sink(y)`` shape that pure forward BFS
    misses: source and sink are both *targets* of the enclosing
    function's call edges, never connected by a forward call. Mirrors
    the intraprocedural assignment-propagation Semgrep ships in
    February 2026.

    Returns a list of ``(enclosing_fn_id, source_id, sink_id,
    sanitizer_in_path)`` tuples.
    """
    if not source_ids or not sink_ids:
        return []
    # Pull every (enclosing, target) edge for which the target is a
    # source, sink, or sanitizer of the rule. Group by enclosing.
    interesting = source_ids | sink_ids | sanitizer_ids
    chunks = []
    interesting_list = list(interesting)
    # batched_in pattern, but local — keep this module dependency-free
    for i in range(0, len(interesting_list), 400):
        chunks.append(interesting_list[i : i + 400])

    enclosing_targets: dict[int, set[int]] = {}
    for chunk in chunks:
        rows = conn.execute(
            f"SELECT source_id, target_id FROM edges "
            f"WHERE {call_or_ref_in_clause()} "
            f"AND target_id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for r in rows:
            enclosing_targets.setdefault(int(r[0]), set()).add(int(r[1]))

    out: list[tuple[int, int, int, bool]] = []
    for enclosing, targets in enclosing_targets.items():
        if not (targets & source_ids) or not (targets & sink_ids):
            continue
        src_id = next(iter(targets & source_ids))
        sink_id = next(iter(targets & sink_ids))
        has_sanitizer = bool(targets & sanitizer_ids)
        out.append((enclosing, src_id, sink_id, has_sanitizer))
    return out


def _collect_findings_for_rule_isolation(
    conn: sqlite3.Connection,
    rule: TaintRule,
    sources: list[dict],
    sinks: list[dict],
    sanitizers: list[dict],
    *,
    max_hops: int,
) -> list[TaintFinding]:
    """Keep co-call and BFS analysis scoped to one resolved taint rule."""
    findings: list[TaintFinding] = []
    source_ids = {s["id"] for s in sources}
    sink_ids = {s["id"] for s in sinks}
    # Drop overlap: a node listed as both a source and a sanitizer
    # would otherwise mark every reachable path as has_sanitizer=True
    # at BFS-start, producing a false `inline_mitigations_already_exist`
    # OpenVEX claim. Sanitizers must be intermediate nodes, not sources.
    sanitizer_ids = {s["id"] for s in sanitizers} - source_ids

    # Path id -> metadata for hop rendering.
    sym_meta: dict[int, dict] = {s["id"]: s for s in sources + sinks + sanitizers}

    # Pass 2 first (cheap): per-function co-call records flow
    # through assignments / locals without needing an edge.
    co_calls = _intraprocedural_co_calls(conn, source_ids, sink_ids, sanitizer_ids)
    for enclosing, src_id, sink_id, has_sanitizer in co_calls:
        unknown = [pid for pid in (enclosing, src_id, sink_id) if pid not in sym_meta]
        if unknown:
            rows = conn.execute(
                "SELECT s.id, s.name, s.qualified_name, s.line_start, f.path "
                "FROM symbols s JOIN files f ON s.file_id = f.id "
                f"WHERE s.id IN ({','.join('?' * len(unknown))})",
                unknown,
            ).fetchall()
            for r in rows:
                sym_meta[int(r[0])] = {
                    "id": int(r[0]),
                    "name": r[1],
                    "qualified_name": r[2],
                    "line": r[3],
                    "file": r[4],
                }
        findings.append(
            TaintFinding(
                rule_id=rule.rule_id,
                severity=rule.severity,
                cwe=rule.cwe,
                source_symbol=sym_meta.get(src_id, {"id": src_id}),
                sink_symbol=sym_meta.get(sink_id, {"id": sink_id}),
                path_symbols=[
                    sym_meta.get(src_id, {"id": src_id}),
                    sym_meta.get(enclosing, {"id": enclosing}),
                    sym_meta.get(sink_id, {"id": sink_id}),
                ],
                sanitizer_in_path=has_sanitizer,
                owasp_top10=rule.owasp_top10,
            )
        )

    path_ids, has_sanitizer, path_truncated = _bfs_path(conn, source_ids, sink_ids, sanitizer_ids, max_hops=max_hops)
    if path_ids is None:
        # No path found within the search bounds. We don't emit a
        # finding because there's no concrete path to point at;
        # the truncated-negative case (search hit a cap so a real
        # path may have been missed) is captured in the per-finding
        # ``path_truncated`` flag for paths that DID resolve, where
        # consumers need to know the search wasn't exhaustive.
        return findings

    # Hydrate any path nodes we don't already have metadata for.
    unknown = [pid for pid in path_ids if pid not in sym_meta]
    if unknown:
        chunk = unknown[:400]
        rows = conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.line_start, f.path "
            "FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for r in rows:
            sym_meta[int(r[0])] = {
                "id": int(r[0]),
                "name": r[1],
                "qualified_name": r[2],
                "line": r[3],
                "file": r[4],
            }

    path_symbols = [sym_meta.get(pid, {"id": pid}) for pid in path_ids]
    findings.append(
        TaintFinding(
            rule_id=rule.rule_id,
            severity=rule.severity,
            cwe=rule.cwe,
            source_symbol=path_symbols[0],
            sink_symbol=path_symbols[-1],
            path_symbols=path_symbols,
            sanitizer_in_path=has_sanitizer,
            path_truncated=path_truncated,
            owasp_top10=rule.owasp_top10,
        )
    )
    return findings


def run_taint(
    conn: sqlite3.Connection,
    rules: list[TaintRule],
    *,
    max_hops: int = 6,
    project_root: str | None = None,
    anchor_stats: dict | None = None,
) -> list[TaintFinding]:
    """Execute every rule against the indexed graph. Returns one finding
    per (rule, source, sink, path) tuple. When a rule's sources never
    reach its sinks, no findings are emitted for that rule.

    Three passes:
    1. Forward BFS for cross-procedural call chains where source and
       sink connect via intermediate hops.
    2. Intraprocedural co-call check for the
       ``y = source(); sink(y)`` shape — functions that *call both* a
       source and a sink are flagged even though no forward edge
       connects them. Mirrors Semgrep's Feb 2026 assignment-propagation
       improvement.
    3. W1330 text-scan fallback (only when *project_root* is given AND
       a rule's DB-indexed sources or sinks came back empty): see
       :func:`_text_scan_rule_anchors`. Closes the W452 indexer gap for
       import-bound Python sources/sinks (``request.args``,
       ``cursor.execute``, ``os.system`` — never materialised as
       ``symbols`` rows because they're not local definitions) without
       touching the general indexer or the ``symbols`` table. Callers
       that never pass *project_root* (every pre-existing direct
       ``run_taint(conn, rules)`` call site) get byte-identical
       behaviour — this is purely additive and opt-in.

    *anchor_stats*, when given an (initially empty) dict, is populated
    with ``{"rules_evaluated": int, "rules_zero_anchors": int,
    "zero_anchor_rule_ids": [...]}`` — the "instrument counted nothing"
    signal from AP-206-213 / task #285: a rule whose sources or sinks
    NEVER resolved to any anchor (DB symbol or text-scan hit) was
    evaluated as inconclusive, not as "scanned and clean." Mirrors the
    ``drop_stats`` out-param convention already used by
    :func:`roam.index.relations.resolve_references`. ``None`` (the
    default) skips the bookkeeping entirely — zero cost for callers
    that don't need it.
    """
    findings: list[TaintFinding] = []
    text_scan_cache: dict[int, tuple[str, list[dict]]] = {}
    zero_anchor_rule_ids: list[str] = []
    for rule in rules:
        sources = _symbols_matching(conn, rule.sources, rule.languages, qualified_only=rule.qualified_only)
        sinks = _symbols_matching(conn, rule.sinks, rule.languages, qualified_only=rule.qualified_only)
        sanitizers = _symbols_matching(conn, rule.sanitizers, rule.languages, qualified_only=rule.qualified_only)

        if project_root and (not sources or not sinks):
            scan = _text_scan_rule_anchors(conn, project_root, rule, text_scan_cache)
            sources = sources + scan["sources"]
            sinks = sinks + scan["sinks"]
            sanitizers = sanitizers + scan["sanitizers"]
            findings.extend(scan["co_occurrence_findings"])

        if not sources or not sinks:
            zero_anchor_rule_ids.append(rule.rule_id)
            continue
        findings.extend(
            _collect_findings_for_rule_isolation(
                conn,
                rule,
                sources,
                sinks,
                sanitizers,
                max_hops=max_hops,
            )
        )

    if anchor_stats is not None:
        anchor_stats["rules_evaluated"] = len(rules)
        anchor_stats["rules_zero_anchors"] = len(zero_anchor_rule_ids)
        anchor_stats["zero_anchor_rule_ids"] = zero_anchor_rule_ids

    return findings


# ---------------------------------------------------------------------------
# W1330 — text-scan fallback anchors (closes the W452 indexer gap for taint)
# ---------------------------------------------------------------------------
#
# ROOT CAUSE (audited 2026-07-15, task #285): the Python indexer records
# function/class definitions and forward call edges between THEM, but never
# materialises import-bound names (``request.args`` from ``from flask
# import request``) or attribute-access chains (``cursor.execute``,
# ``os.system``) as rows in the ``symbols`` table — they're not local
# definitions, so there's nothing to index. ``_symbols_matching`` above
# only ever looks at ``symbols``, so a rule whose source/sink vocabulary is
# entirely import-bound (true of nearly every real Flask/Django taint
# rule) matches ZERO rows and the rule is silently skipped
# (``if not sources or not sinks: continue``) — 0 findings that look
# identical to "scanned and clean."
#
# Rather than growing the general indexer (new symbol kind, wider
# ``edges`` semantics, blast radius across every other command that reads
# ``symbols``/``edges``), this fallback stays entirely inside the taint
# engine: when a rule's DB-indexed anchors come back empty, re-read the
# already-indexed Python files from disk and regex-scan (comments/strings
# masked via the same state machine ``index/relations.py`` uses for W167
# import verification) for the rule's literal source/sink/sanitizer
# names. Each hit is anchored to its ENCLOSING function/method via the
# already-indexed ``line_start``/``line_end`` span — that real symbol id
# feeds the existing forward-BFS pass unchanged (cross-function reach,
# e.g. ``search()`` calling ``run_query()``). Same-function occurrences
# (source and sink text both inside one function body, e.g. a Flask route
# handler) can't be expressed as "enclosing calls both" — the enclosing
# node in that shape IS the occurrence — so they're detected directly as
# ``co_occurrence_findings`` rather than routed through the co-call BFS.
#
# Deliberately narrow: Python-only for now (the exact shape W452 named),
# opt-in via `project_root` (every existing direct ``run_taint(conn,
# rules)`` call site is unaffected), and only engaged as a fallback when
# the DB path found nothing — a rule that already resolves real symbols
# never has its behaviour changed by this pass.

_TEXT_SCAN_LANGUAGES: frozenset[str] = frozenset({"python"})
_WORD_CHAR_CLASS = "[A-Za-z0-9_]"
_dotted_pattern_cache: dict[str, re.Pattern[str]] = {}


def _dotted_name_pattern(name: str) -> re.Pattern[str]:
    """Compile (and cache) a word-boundary regex for a literal dotted name.

    Blocks a preceding/trailing identifier character so ``cursor.execute``
    doesn't match inside ``mycursor.execute_batch``, but tolerates a
    preceding ``.`` so ``self.cursor.execute`` still counts — mirroring
    the suffix tolerance ``_symbols_matching`` already applies via its
    ``qualified_name LIKE '%.<name>'`` branch.
    """
    pat = _dotted_pattern_cache.get(name)
    if pat is None:
        pat = re.compile(rf"(?<!{_WORD_CHAR_CLASS}){re.escape(name)}(?!{_WORD_CHAR_CLASS})")
        _dotted_pattern_cache[name] = pat
    return pat


def _text_scan_python_files(conn: sqlite3.Connection, languages: Iterable[str]) -> list[tuple[int, str]]:
    """Return ``(file_id, path)`` for indexed Python files eligible for scan.

    Empty ``languages`` on the rule (applies to any language) still scopes
    to :data:`_TEXT_SCAN_LANGUAGES` — this fallback only knows how to
    anchor hits to Python's function/method symbol shape today.
    """
    lang_list = list(languages)
    scan_langs = (set(lang_list) & _TEXT_SCAN_LANGUAGES) if lang_list else set(_TEXT_SCAN_LANGUAGES)
    if not scan_langs:
        return []
    placeholders = ",".join("?" for _ in scan_langs)
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE language IN ({placeholders})",
        list(scan_langs),
    ).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def _enclosing_candidates_for_file(conn: sqlite3.Connection, file_id: int) -> list[dict]:
    """Function/method symbols in *file_id*, for line-range anchoring."""
    rows = conn.execute(
        "SELECT id, name, qualified_name, line_start, line_end FROM symbols "
        "WHERE file_id = ? AND kind IN ('function', 'method') AND line_start IS NOT NULL",
        (file_id,),
    ).fetchall()
    return [
        {
            "id": int(r[0]),
            "name": r[1],
            "qualified_name": r[2] or r[1],
            "line_start": int(r[3]),
            "line_end": int(r[4]) if r[4] is not None else int(r[3]),
        }
        for r in rows
    ]


def _innermost_enclosing(candidates: list[dict], line: int) -> dict | None:
    """Smallest-span symbol whose ``[line_start, line_end]`` contains *line*."""
    best: dict | None = None
    for sym in candidates:
        if sym["line_start"] <= line <= sym["line_end"]:
            if best is None or (sym["line_end"] - sym["line_start"]) < (best["line_end"] - best["line_start"]):
                best = sym
    return best


def _masked_text_and_symbols_for_file(
    conn: sqlite3.Connection,
    project_root: str,
    file_id: int,
    path: str,
    cache: dict[int, tuple[str, list[dict]]],
) -> tuple[str, list[dict]] | None:
    """Read + mask a file's source once per ``run_taint`` call, cached by file id.

    Several python-* rules typically share the same fallback pass in one
    invocation (sqli, ssti, deserialization, command-injection all fire
    on real Flask code) — caching keeps the file I/O + comment/string
    masking to O(files) instead of O(rules x files).
    """
    cached = cache.get(file_id)
    if cached is not None:
        return cached
    text = _read_source_text(path, project_root)
    if not text:
        return None
    masked = _mask_strings_and_comments(text)
    candidates = _enclosing_candidates_for_file(conn, file_id)
    entry = (masked, candidates)
    cache[file_id] = entry
    return entry


def _call_args_span(masked_text: str, call_end: int) -> str | None:
    """Return the argument-list text of a call starting right after
    *call_end* (the offset just past the callee name), or ``None`` if
    *call_end* isn't immediately followed by ``(`` (not a call at all —
    e.g. the sink name was passed as a bare reference).

    Depth-scanned on the ALREADY comment/string-masked text so a literal
    ``(`` or ``,`` inside a SQL string (``"SELECT f(x)"``) can't skew the
    paren-depth count or look like a second argument.
    """
    n = len(masked_text)
    i = call_end
    while i < n and masked_text[i] in " \t":
        i += 1
    if i >= n or masked_text[i] != "(":
        return None
    depth = 0
    j = i
    while j < n:
        ch = masked_text[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return masked_text[i + 1 : j]
        j += 1
    return masked_text[i + 1 : j]  # unterminated — best effort


def _has_top_level_char(args_text: str, target: str) -> bool:
    """True if *target* appears in *args_text* outside any nested bracket."""
    depth = 0
    for ch in args_text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == target and depth == 0:
            return True
    return False


def _looks_like_parameterized_db_call(masked_text: str, call_end: int) -> bool:
    """Heuristic: does the call right after *call_end* look like a
    parameterized DB call (``execute(sql, params)``) rather than a
    concatenated/formatted SQL string (``execute(sql + user_input)``)?

    Scoped narrowly to *sinks whose name contains "execute"* (the whole
    ``python-sqli`` sink vocabulary: ``cursor.execute`` / ``conn.execute``
    / ``db.execute`` / ``connection.execute`` / ``session.execute``) —
    Python's DB-API idiom passes bind parameters as a SEPARATE positional
    argument (``execute(sql, (value,))``), unlike ``os.system`` /
    ``pickle.loads`` / ``render_template_string``, which have no "safe
    argument shape": any tainted argument to those IS the vulnerability,
    so this heuristic must never suppress a hit for them.

    A top-level comma (a second argument) with no top-level ``+``
    (no on-the-fly string concatenation feeding the SQL text) reads as
    the parameterized idiom. Anything else — no second argument at all,
    or a ``+`` anywhere at the top level — stays flagged. Conservative
    by construction: this can only SUPPRESS a text-scan sink hit, never
    manufacture one, so it cannot introduce a false negative on a rule
    that would otherwise have zero anchors.
    """
    args_text = _call_args_span(masked_text, call_end)
    if args_text is None:
        return False
    return _has_top_level_char(args_text, ",") and not _has_top_level_char(args_text, "+")


def _scan_hits(masked_text: str, names: Iterable[str]) -> list[tuple[str, int]]:
    """Return ``(matched_name, 1-based_line)`` for every pattern occurrence.

    Skips a hit when :func:`_looks_like_parameterized_db_call` says the
    call looks like the safe ``execute(sql, params)`` idiom — see that
    function's docstring for why this is scoped to "execute"-named
    sinks only.
    """
    hits: list[tuple[str, int]] = []
    for name in names:
        if not name:
            continue
        # W1446 — substring pre-check before the regex pass.
        #
        # `_dotted_name_pattern` compiles to (?<!\w)re.escape(name)(?!\w), so a
        # LITERAL occurrence of `name` in the text is a strict NECESSARY
        # condition for any match: the pattern adds only boundary lookarounds,
        # never widens what can match. `name not in masked_text` therefore
        # cannot skip a hit the regex would have found.
        #
        # Why it matters: this function is called three times per (rule, file)
        # — sources, sinks, sanitizers — and each call ran one whole-file
        # regex scan per name. Measured on this repo: 292,221 scans, of which
        # 286,659 (98.1%) matched nothing. Swapping those for a C-level
        # substring scan took `taint` 85.7s -> 15.5s and the whole
        # reachability-triage service report 87.3s -> 27.5s, with byte-identical
        # findings output.
        if name not in masked_text:
            continue
        is_execute_sink = "execute" in name.lower()
        for m in _dotted_name_pattern(name).finditer(masked_text):
            if is_execute_sink and _looks_like_parameterized_db_call(masked_text, m.end()):
                continue
            line = masked_text.count("\n", 0, m.start()) + 1
            hits.append((name, line))
    return hits


def _dotted_names_only(names: Iterable[str]) -> list[str]:
    """Filter to qualified (dotted) names only — text-scan SOURCE matching
    uses this, sinks/sanitizers don't.

    A bare single-word source name (``data``, ``payload``, ``input`` —
    ``python_socketio_remote_source.yaml`` lists exactly this shape) has
    no import-bound anchor: it's just as likely to be a coincidental
    local variable/parameter name as it is the actual attacker-controlled
    value the rule means. A DB-indexed ``symbols`` row for that bare name
    at least required someone to DEFINE something with that exact name;
    a text-scan hit requires nothing more than the word appearing
    anywhere in the file, which is far too permissive for single common
    words. Every source in the rules that motivated this fallback
    (python-sqli/ssti/deserialization/command-injection) is already a
    qualified name (``request.args``, ``request.data``, ...), so this
    costs nothing there. Sinks stay unrestricted: a sink is always
    something CALLED, so a bare match like ``render_template_string(...)``
    or ``eval(...)`` is a real invocation, not an incidental variable read.
    """
    return [n for n in names if n and "." in n]


def _hit_to_anchor(hit: tuple[str, int], enclosing: dict | None, path: str) -> dict | None:
    """Project one text hit to the ``_symbols_matching``-shaped anchor dict.

    ``id`` is the REAL enclosing symbol id (not a synthetic row) so the
    hit slots straight into the existing forward-BFS / co-call machinery
    unchanged. Hits with no enclosing function (module-level code, or a
    match inside a docstring/comment before masking removed it) are
    dropped — there's no graph node to anchor them to.
    """
    if enclosing is None:
        return None
    name, line = hit
    return {
        "id": enclosing["id"],
        "name": name.rsplit(".", 1)[-1],
        "qualified_name": name,
        "line": line,
        "file": path,
        "_enclosing_id": enclosing["id"],
    }


def _text_scan_rule_anchors(
    conn: sqlite3.Connection,
    project_root: str,
    rule: TaintRule,
    cache: dict[int, tuple[str, list[dict]]],
) -> dict:
    """Text-scan indexed Python files for *rule*'s source/sink/sanitizer names.

    Returns ``{"sources": [...], "sinks": [...], "sanitizers": [...],
    "co_occurrence_findings": [TaintFinding, ...]}``. The three anchor
    lists carry REAL enclosing-symbol ids for the caller to union into
    the forward-BFS pass. ``co_occurrence_findings`` are built directly
    here for the same-function shape (source and sink text both inside
    one function body) that forward BFS structurally cannot express —
    see the module-level W1330 docstring above.
    """
    empty: dict = {"sources": [], "sinks": [], "sanitizers": [], "co_occurrence_findings": []}
    files = _text_scan_python_files(conn, rule.languages)
    if not files:
        return empty

    out_sources: list[dict] = []
    out_sinks: list[dict] = []
    out_sanitizers: list[dict] = []
    co_findings: list[TaintFinding] = []

    for file_id, path in files:
        loaded = _masked_text_and_symbols_for_file(conn, project_root, file_id, path, cache)
        if loaded is None:
            continue
        masked, candidates = loaded

        src_hits = _scan_hits(masked, _dotted_names_only(rule.sources))
        sink_hits = _scan_hits(masked, rule.sinks)
        san_hits = _scan_hits(masked, rule.sanitizers)
        if not (src_hits or sink_hits or san_hits):
            continue

        src_anchors = [
            a for a in (_hit_to_anchor(h, _innermost_enclosing(candidates, h[1]), path) for h in src_hits) if a
        ]
        sink_anchors = [
            a for a in (_hit_to_anchor(h, _innermost_enclosing(candidates, h[1]), path) for h in sink_hits) if a
        ]
        san_anchors = [
            a for a in (_hit_to_anchor(h, _innermost_enclosing(candidates, h[1]), path) for h in san_hits) if a
        ]

        out_sources.extend(src_anchors)
        out_sinks.extend(sink_anchors)
        out_sanitizers.extend(san_anchors)

        sinks_by_enclosing: dict[int, list[dict]] = {}
        for a in sink_anchors:
            sinks_by_enclosing.setdefault(a["_enclosing_id"], []).append(a)
        sanitized_enclosing_ids = {a["_enclosing_id"] for a in san_anchors}

        seen_pairs: set[tuple[int, int]] = set()
        candidates_by_id = {c["id"]: c for c in candidates}
        for src_anchor in src_anchors:
            eid = src_anchor["_enclosing_id"]
            for sink_anchor in sinks_by_enclosing.get(eid, ()):
                pair_key = (src_anchor["line"], sink_anchor["line"])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                enclosing_sym = candidates_by_id.get(eid, {})
                enclosing_dict = {
                    "id": eid,
                    "name": enclosing_sym.get("name"),
                    "qualified_name": enclosing_sym.get("qualified_name"),
                    "line": enclosing_sym.get("line_start"),
                    "file": path,
                }
                source_symbol = {k: v for k, v in src_anchor.items() if k != "_enclosing_id"}
                sink_symbol = {k: v for k, v in sink_anchor.items() if k != "_enclosing_id"}
                co_findings.append(
                    TaintFinding(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        cwe=rule.cwe,
                        source_symbol=source_symbol,
                        sink_symbol=sink_symbol,
                        path_symbols=[source_symbol, enclosing_dict, sink_symbol],
                        sanitizer_in_path=eid in sanitized_enclosing_ids,
                        owasp_top10=rule.owasp_top10,
                    )
                )

    return {
        "sources": out_sources,
        "sinks": out_sinks,
        "sanitizers": out_sanitizers,
        "co_occurrence_findings": co_findings,
    }


def vex_justification_for(finding: TaintFinding) -> str:
    """Map a TaintFinding to one of the five spec-legal OpenVEX
    justification strings.

    The mapping intentionally never produces ``code_not_reachable`` —
    that string is **not** in the spec and would make every downstream
    VEX consumer reject the document.

    * Sanitized path → ``inline_mitigations_already_exist``
    * Reachable source → sink → return ``""`` (the finding is *affected*
      / not a *not_affected* claim — caller maps to status, not
      justification).

    The "no path exists" / "package not present" cases live in the
    private ``_vex_justification_for_unreachable`` helper.
    """
    if finding.sanitizer_in_path:
        return "inline_mitigations_already_exist"
    return ""


def _vex_justification_for_unreachable(*, package_present: bool) -> str:
    """Return the justification string for a not_affected finding."""
    if not package_present:
        return "component_not_present"
    return "vulnerable_code_not_in_execute_path"


def __getattr__(name: str) -> object:
    """Resolve legacy direct imports without exporting the helper."""
    if name == "vex_justification_for_unreachable":
        return _vex_justification_for_unreachable
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
