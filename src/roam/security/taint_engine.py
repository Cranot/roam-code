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

import sqlite3
import warnings
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from roam.commands._yaml_loader import load_yaml_with_warnings
from roam.db.edge_kinds import call_or_ref_in_clause
from roam.output._severity import validate_severity

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
            for kind, entries in (
                ("sources", sources),
                ("sinks", sinks),
                ("sanitizers", sanitizers),
            ):
                for name in entries:
                    if "." not in str(name):
                        warnings.warn(
                            f"[taint-engine] rule {rule_id!r}: bare {kind[:-1]} "
                            f"{name!r} is a no-op under qualified_only=true; "
                            f"either qualify it or drop qualified_only",
                            stacklevel=2,
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

    queue: deque[tuple[int, list[int], bool]] = deque((s, [s], s in sanitizer_ids) for s in start_ids)
    visited: set[int] = set(start_ids)
    truncated = False

    while queue:
        node, path, has_sanitizer = queue.popleft()
        if node in goal_ids and node not in start_ids:
            return path, has_sanitizer, truncated
        if len(path) > max_hops:
            # We're bumping the hop ceiling — record and skip expansion.
            truncated = True
            continue

        # W512: edge-kind vocabulary lives in roam.db.edge_kinds. W79 fix
        # surfaced by W78.
        rows = conn.execute(
            f"SELECT target_id FROM edges WHERE source_id = ? AND {call_or_ref_in_clause()} LIMIT ?",
            (node, _BFS_FAN_OUT_LIMIT),
        ).fetchall()
        if len(rows) >= _BFS_FAN_OUT_LIMIT:
            # Hit the per-node fan-out cap — the path may exist beyond
            # the truncated edge set. Mark and proceed (don't propagate
            # within this branch — other branches may still find a path).
            truncated = True
        for row in rows:
            tgt = int(row[0])
            if tgt in visited:
                continue
            visited.add(tgt)
            queue.append((tgt, path + [tgt], has_sanitizer or tgt in sanitizer_ids))

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


def run_taint(
    conn: sqlite3.Connection,
    rules: list[TaintRule],
    *,
    max_hops: int = 6,
) -> list[TaintFinding]:
    """Execute every rule against the indexed graph. Returns one finding
    per (rule, source, sink, path) tuple. When a rule's sources never
    reach its sinks, no findings are emitted for that rule.

    Two passes:
    1. Forward BFS for cross-procedural call chains where source and
       sink connect via intermediate hops.
    2. Intraprocedural co-call check for the
       ``y = source(); sink(y)`` shape — functions that *call both* a
       source and a sink are flagged even though no forward edge
       connects them. Mirrors Semgrep's Feb 2026 assignment-propagation
       improvement.
    """
    findings: list[TaintFinding] = []
    for rule in rules:
        sources = _symbols_matching(conn, rule.sources, rule.languages, qualified_only=rule.qualified_only)
        sinks = _symbols_matching(conn, rule.sinks, rule.languages, qualified_only=rule.qualified_only)
        sanitizers = _symbols_matching(conn, rule.sanitizers, rule.languages, qualified_only=rule.qualified_only)
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

        path_ids, has_sanitizer, path_truncated = _bfs_path(
            conn, source_ids, sink_ids, sanitizer_ids, max_hops=max_hops
        )
        if path_ids is None:
            # No path found within the search bounds. We don't emit a
            # finding because there's no concrete path to point at;
            # the truncated-negative case (search hit a cap so a real
            # path may have been missed) is captured in the per-finding
            # ``path_truncated`` flag for paths that DID resolve, where
            # consumers need to know the search wasn't exhaustive.
            continue

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
