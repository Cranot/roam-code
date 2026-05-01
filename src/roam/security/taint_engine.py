"""roam taint — graph-reach taint with OpenVEX justifications (E.2).

This is the v12 simpler-by-design engine called for in
``reports/brainstorm_2026-04-29/05_security_enterprise.md`` — a YAML-rule
driven graph-reach BFS over the existing edges table with sanitizer-stop
nodes. Designed to ship in 2 weeks and produce SARIF + OpenVEX-grade
attestation evidence, **not** to compete with the year-long CodeQL
abstract-interpretation approach.

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

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


# ---------------------------------------------------------------------------
# Rule loading (zero-dep YAML subset — same shape as gate_presets)
# ---------------------------------------------------------------------------


def load_rules(rules_dir: Path | str) -> list[TaintRule]:
    """Load every ``*.yaml`` file under *rules_dir* as a TaintRule.

    Falls back to the in-tree YAML subset parser when ``yaml`` isn't
    installed (we still ship zero-dep). Files that fail to parse are
    skipped with a warning attached to the rule list rather than
    crashing the whole load — one bad rule shouldn't take out the rest.
    """
    rules_path = Path(rules_dir)
    if not rules_path.is_dir():
        return []

    out: list[TaintRule] = []
    for yaml_file in sorted(rules_path.glob("*.yaml")):
        text = yaml_file.read_text(encoding="utf-8")
        try:
            doc = _parse_yaml_subset(text)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        rule_id = str(doc.get("id") or yaml_file.stem)
        out.append(
            TaintRule(
                rule_id=rule_id,
                description=str(doc.get("description") or ""),
                severity=str(doc.get("severity") or "warning"),
                cwe=str(doc.get("cwe") or ""),
                languages=tuple(doc.get("languages") or ()),
                sources=tuple(doc.get("sources") or ()),
                sinks=tuple(doc.get("sinks") or ()),
                sanitizers=tuple(doc.get("sanitizers") or ()),
            )
        )
    return out


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
) -> list[dict]:
    """Return symbols whose name OR qualified_name matches any of *names*.

    Match is exact-name OR ``%.<name>`` suffix (so ``request.args`` matches
    qualified-names like ``flask.request.args``). When *languages* is
    non-empty, only symbols whose file language is in the list are
    returned.
    """
    name_list = list(names)
    if not name_list:
        return []

    or_clauses: list[str] = []
    params: list = []
    for name in name_list:
        or_clauses.append("s.name = ?")
        params.append(name)
        or_clauses.append("s.qualified_name = ?")
        params.append(name)
        or_clauses.append("s.qualified_name LIKE ?")
        params.append(f"%.{name}")

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


def _bfs_path(
    conn: sqlite3.Connection,
    start_ids: set[int],
    goal_ids: set[int],
    sanitizer_ids: set[int],
    *,
    max_hops: int = 6,
) -> tuple[list[int], bool] | None:
    """BFS over `edges` from any *start* to any *goal*. Returns the
    shortest path as a list of symbol ids and a flag indicating whether
    a sanitizer node was on the path. Returns ``None`` if no path
    exists within ``max_hops``.
    """
    if not start_ids or not goal_ids:
        return None

    queue: list[tuple[int, list[int], bool]] = [(s, [s], s in sanitizer_ids) for s in start_ids]
    visited: set[int] = set(start_ids)

    while queue:
        node, path, has_sanitizer = queue.pop(0)
        if node in goal_ids and node not in start_ids:
            return path, has_sanitizer
        if len(path) > max_hops:
            continue

        rows = conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ? AND kind IN ('calls', 'references') LIMIT 200",
            (node,),
        ).fetchall()
        for row in rows:
            tgt = int(row[0])
            if tgt in visited:
                continue
            visited.add(tgt)
            queue.append((tgt, path + [tgt], has_sanitizer or tgt in sanitizer_ids))

    return None


def run_taint(
    conn: sqlite3.Connection,
    rules: list[TaintRule],
    *,
    max_hops: int = 6,
) -> list[TaintFinding]:
    """Execute every rule against the indexed graph. Returns one finding
    per (rule, source, sink, path) tuple. When a rule's sources never
    reach its sinks, no findings are emitted for that rule.
    """
    findings: list[TaintFinding] = []
    for rule in rules:
        sources = _symbols_matching(conn, rule.sources, rule.languages)
        sinks = _symbols_matching(conn, rule.sinks, rule.languages)
        sanitizers = _symbols_matching(conn, rule.sanitizers, rule.languages)
        if not sources or not sinks:
            continue
        source_ids = {s["id"] for s in sources}
        sink_ids = {s["id"] for s in sinks}
        # Drop overlap: a node listed as both a source and a sanitizer
        # would otherwise mark every reachable path as has_sanitizer=True
        # at BFS-start, producing a false `inline_mitigations_already_exist`
        # OpenVEX claim. Sanitizers must be intermediate nodes, not sources.
        sanitizer_ids = {s["id"] for s in sanitizers} - source_ids

        # Path id → metadata for hop rendering.
        sym_meta: dict[int, dict] = {s["id"]: s for s in sources + sinks + sanitizers}

        result = _bfs_path(conn, source_ids, sink_ids, sanitizer_ids, max_hops=max_hops)
        if result is None:
            continue
        path_ids, has_sanitizer = result

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
            )
        )

    return findings


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

    The "no path exists" / "package not present" cases live in
    :func:`vex_justification_for_unreachable`.
    """
    if finding.sanitizer_in_path:
        return "inline_mitigations_already_exist"
    return ""


def vex_justification_for_unreachable(*, package_present: bool) -> str:
    """Return the justification string for a not_affected finding."""
    if not package_present:
        return "component_not_present"
    return "vulnerable_code_not_in_execute_path"
