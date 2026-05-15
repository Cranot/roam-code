"""Inter-procedural taint analysis for roam-code.

Computes per-function taint summaries (which parameters flow to return
values or dangerous sinks) and then propagates those summaries across
the call graph to find cross-function source-to-sink flows.

The analysis is deliberately lightweight: intra-procedural tracking is
line-by-line string matching (no full SSA), while inter-procedural
propagation walks call-graph edges up to a configurable depth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from roam.db.edge_kinds import CALL_EDGE_KINDS

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TaintSummary:
    """Per-function summary of taint flow behaviour."""

    symbol_id: int
    param_taints_return: dict[int, bool] = field(default_factory=dict)
    param_to_sink: dict[int, list[str]] = field(default_factory=dict)
    return_from_source: bool = False
    direct_sources: list[str] = field(default_factory=list)
    direct_sinks: list[str] = field(default_factory=list)
    is_sanitizer: bool = False


@dataclass
class TaintFinding:
    """A cross-function taint finding: source flows to sink across calls."""

    source_symbol_id: int
    sink_symbol_id: int
    source_type: str
    sink_type: str
    call_chain: list[int] = field(default_factory=list)
    confidence: float = 0.8


# ---------------------------------------------------------------------------
# Default source / sink / sanitizer patterns
# ---------------------------------------------------------------------------

_DEFAULT_SOURCES = (
    "input(",
    "request.args",
    "request.form",
    "request.get",
    "request.GET",
    "request.POST",
    "sys.argv",
    "os.environ",
    "query_params",
    "params.get",
    "req.body",
    "req.params",
    "req.query",
    "document.location",
    "window.location",
    "process.env",
    "Scanner(System.in",
    "BufferedReader(",
    "getParameter(",
    "getQueryString(",
    "getHeader(",
)

_DEFAULT_SINKS = (
    "eval(",
    "exec(",
    "os.system(",
    "subprocess.run(",
    "subprocess.popen(",
    "pickle.loads(",
    "yaml.load(",
    "Function(",
    "innerHTML",
    "document.write(",
    "cursor.execute(",
    ".execute(",
    "render_template_string(",
    "send_file(",
    "redirect(",
    "open(",
    "Runtime.exec(",
    "ProcessBuilder(",
    "child_process.exec(",
    "shell_exec(",
)

_SANITIZER_NAMES = frozenset(
    {
        "escape",
        "sanitize",
        "validate",
        "encode",
        "clean",
        "filter",
        "strip",
        "purify",
        "quote",
        "parameterize",
        "bleach",
        "markupsafe",
        "html_escape",
        "urlencode",
        "escape_string",
        "sqlquote",
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")

_ASSIGN_PATTERNS = (
    re.compile(r"^\s*(?:let|const|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*="),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)"),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[\+\-\*/%]="),
)

_IGNORED_PARAMS = {"_", "self", "cls", "this"}


def _detect_sanitizer(name: str, qualified_name: str | None = None) -> bool:
    """Return True if the symbol name suggests a sanitizer function."""
    lower = (name or "").lower()
    qn = (qualified_name or "").lower()
    return any(s in lower or s in qn for s in _SANITIZER_NAMES)


def _parse_param_names(signature: str | None) -> list[str]:
    """Extract parameter names from a signature string like ``def foo(a, b=1)``."""
    if not signature:
        return []
    m = re.search(r"\(([^)]*)\)", signature)
    if not m:
        return []
    params_str = m.group(1).strip()
    if not params_str:
        return []

    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in params_str:
        if ch in "([{<":
            depth += 1
            current.append(ch)
        elif ch in ")]}>":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())

    names: list[str] = []
    for part in parts:
        token = part
        while token.startswith("*"):
            token = token[1:]
        token = token.split(":", 1)[0].split("=", 1)[0].strip()
        if token and token not in _IGNORED_PARAMS:
            names.append(token)
    return names


# ---------------------------------------------------------------------------
# Intra-procedural variable taint tracking
# ---------------------------------------------------------------------------


TaintOrigin = tuple[str, Union[int, str]]


@dataclass
class _TaintTrackState:
    param_taints_return: dict[int, bool] = field(default_factory=dict)
    param_to_sink: dict[int, list[str]] = field(default_factory=dict)
    return_from_source: bool = False
    found_sources: list[str] = field(default_factory=list)
    found_sinks: list[str] = field(default_factory=list)


def _initial_tainted_vars(param_names: list[str]) -> dict[str, set[TaintOrigin]]:
    return {pname: {("param", idx)} for idx, pname in enumerate(param_names)}


def _line_has_sanitizer(line_lower: str, sanitizer_names: frozenset[str]) -> bool:
    return any(sname in line_lower for sname in sanitizer_names)


def _assigned_var(line: str) -> str | None:
    for pat in _ASSIGN_PATTERNS:
        match = pat.search(line)
        if match:
            return match.group(1).strip()
    return None


def _assignment_rhs(line: str, assigned_var: str | None) -> str:
    if assigned_var and "=" in line:
        return line[line.index("=") + 1 :]
    return line


def _rhs_taint(
    rhs: str,
    tainted_vars: dict[str, set[TaintOrigin]],
    sources: tuple[str, ...],
    state: _TaintTrackState,
) -> set[TaintOrigin]:
    out: set[TaintOrigin] = set()
    rhs_lower = rhs.lower()
    for src in sources:
        if src.lower() in rhs_lower:
            out.add(("source", src))
            if src not in state.found_sources:
                state.found_sources.append(src)
            break

    for ident in set(_IDENT_RE.findall(rhs)):
        if ident in tainted_vars:
            out.update(tainted_vars[ident])
    return out


def _apply_assignment_taint(
    tainted_vars: dict[str, set[TaintOrigin]],
    assigned_var: str | None,
    rhs_taint: set[TaintOrigin],
    is_sanitize_line: bool,
) -> None:
    if not assigned_var:
        return
    if is_sanitize_line:
        tainted_vars.pop(assigned_var, None)
    elif rhs_taint:
        tainted_vars[assigned_var] = rhs_taint
    else:
        tainted_vars.pop(assigned_var, None)


def _record_return_taint(line: str, tainted_vars: dict[str, set[TaintOrigin]], state: _TaintTrackState) -> None:
    if not (line.startswith("return ") or line == "return"):
        return
    for ident in set(_IDENT_RE.findall(line[6:])):
        if ident not in tainted_vars:
            continue
        for origin in tainted_vars[ident]:
            if origin[0] == "param":
                state.param_taints_return[int(origin[1])] = True
            elif origin[0] == "source":
                state.return_from_source = True


def _record_sink_taint(
    line: str,
    line_lower: str,
    tainted_vars: dict[str, set[TaintOrigin]],
    sinks: tuple[str, ...],
    state: _TaintTrackState,
) -> None:
    for sink in sinks:
        if sink.lower() not in line_lower:
            continue
        if sink not in state.found_sinks:
            state.found_sinks.append(sink)
        sink_idents = set(_IDENT_RE.findall(line))
        for ident in sink_idents:
            if ident not in tainted_vars:
                continue
            for origin in tainted_vars[ident]:
                if origin[0] != "param":
                    continue
                pidx = int(origin[1])
                state.param_to_sink.setdefault(pidx, [])
                if sink not in state.param_to_sink[pidx]:
                    state.param_to_sink[pidx].append(sink)
        break


def _track_variable_taint(
    body_lines: list[str],
    param_names: list[str],
    sources: tuple[str, ...],
    sinks: tuple[str, ...],
    sanitizer_names: frozenset[str],
) -> dict:
    """Line-by-line variable taint tracking within a single function.

    Returns a dict with keys:
        param_taints_return: {param_idx: True/False}
        param_to_sink: {param_idx: [sink_pattern, ...]}
        return_from_source: bool
        direct_sources: [source_pattern, ...]
        direct_sinks: [sink_pattern, ...]
    """
    # tainted_vars maps variable name -> set of taint origins
    # An origin is either ("param", idx) or ("source", pattern_str)
    tainted_vars = _initial_tainted_vars(param_names)
    state = _TaintTrackState()

    for raw_line in body_lines:
        line = raw_line.strip()
        line_lower = line.lower()

        assigned_var = _assigned_var(line)
        rhs_taint = _rhs_taint(_assignment_rhs(line, assigned_var), tainted_vars, sources, state)
        _apply_assignment_taint(tainted_vars, assigned_var, rhs_taint, _line_has_sanitizer(line_lower, sanitizer_names))
        _record_return_taint(line, tainted_vars, state)
        _record_sink_taint(line, line_lower, tainted_vars, sinks, state)

    return {
        "param_taints_return": state.param_taints_return,
        "param_to_sink": state.param_to_sink,
        "return_from_source": state.return_from_source,
        "direct_sources": state.found_sources,
        "direct_sinks": state.found_sinks,
    }


def compute_intra_summary(
    conn,
    symbol_id: int,
    body_lines: list[str],
    signature: str | None,
    param_names: list[str],
    sources: tuple[str, ...],
    sinks: tuple[str, ...],
) -> TaintSummary:
    """Compute intra-procedural taint summary for a single symbol."""
    # Fetch name for sanitizer detection
    row = conn.execute(
        "SELECT name, qualified_name FROM symbols WHERE id = ?",
        (symbol_id,),
    ).fetchone()
    sym_name = row["name"] if row else ""
    sym_qname = row["qualified_name"] if row else None

    is_sanitizer = _detect_sanitizer(sym_name, sym_qname)

    if not body_lines:
        return TaintSummary(
            symbol_id=symbol_id,
            is_sanitizer=is_sanitizer,
        )

    result = _track_variable_taint(
        body_lines,
        param_names,
        sources,
        sinks,
        _SANITIZER_NAMES,
    )

    return TaintSummary(
        symbol_id=symbol_id,
        param_taints_return=result["param_taints_return"],
        param_to_sink=result["param_to_sink"],
        return_from_source=result["return_from_source"],
        direct_sources=result["direct_sources"],
        direct_sinks=result["direct_sinks"],
        is_sanitizer=is_sanitizer,
    )


# ---------------------------------------------------------------------------
# Batch intra-procedural analysis
# ---------------------------------------------------------------------------


def compute_all_summaries(
    conn,
    root: Path,
    *,
    sources: tuple[str, ...] | None = None,
    sinks: tuple[str, ...] | None = None,
    source_cache=None,
) -> dict[int, TaintSummary]:
    """Compute intra-procedural taint summaries for all functions/methods.

    Args:
        source_cache: Optional ``{rel_path: (source_bytes, tree)}`` mapping
            populated upstream during Phase 2 (parse_extract). When the file
            is present in the cache, the cached bytes are reused instead of
            re-opening the file from disk (W440). Defaults to ``None``
            (backwards-compatible).
    """
    src = sources or _DEFAULT_SOURCES
    snk = sinks or _DEFAULT_SINKS

    rows = conn.execute(
        """
        SELECT s.id, s.signature, s.line_start, s.line_end,
               s.name, s.qualified_name,
               f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method', 'constructor')
          AND s.line_start IS NOT NULL
          AND s.line_end IS NOT NULL
        ORDER BY f.path, s.line_start
        """
    ).fetchall()

    summaries: dict[int, TaintSummary] = {}
    file_cache: dict[str, list[str]] = {}

    for row in rows:
        sym_id = row["id"]
        rel_path = row["file_path"] or ""
        ls = row["line_start"] or 1
        le = row["line_end"] or ls
        signature = row["signature"]
        sym_name = row["name"] or ""
        sym_qname = row["qualified_name"]

        # Read file (cached)
        all_lines = file_cache.get(rel_path)
        if all_lines is None:
            # W440: prefer the upstream (source_bytes, tree) cache from
            # Phase 2 before falling back to a fresh disk read.
            cached = source_cache.get(rel_path) if source_cache else None
            if cached is not None:
                src_bytes = cached[0]
                try:
                    all_lines = src_bytes.decode("utf-8", errors="replace").splitlines()
                except Exception:
                    all_lines = []
            else:
                full_path = root / rel_path
                try:
                    text = full_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    all_lines = []
                else:
                    all_lines = text.splitlines()
            file_cache[rel_path] = all_lines

        # Body lines (after declaration line)
        body = all_lines[ls : min(le, len(all_lines))] if all_lines else []

        param_names = _parse_param_names(signature)
        is_sanitizer = _detect_sanitizer(sym_name, sym_qname)

        if not body:
            summaries[sym_id] = TaintSummary(symbol_id=sym_id, is_sanitizer=is_sanitizer)
            continue

        result = _track_variable_taint(body, param_names, src, snk, _SANITIZER_NAMES)

        summaries[sym_id] = TaintSummary(
            symbol_id=sym_id,
            param_taints_return=result["param_taints_return"],
            param_to_sink=result["param_to_sink"],
            return_from_source=result["return_from_source"],
            direct_sources=result["direct_sources"],
            direct_sinks=result["direct_sinks"],
            is_sanitizer=is_sanitizer,
        )

    return summaries


# ---------------------------------------------------------------------------
# Inter-procedural propagation
# ---------------------------------------------------------------------------


def _build_call_adjacency(conn) -> dict[int, list[int]]:
    """Build a source_id → list[target_id] adjacency for call edges.
    One round-trip to the DB.

    W512: edge-kind vocabulary lives in :mod:`roam.db.edge_kinds`. The
    canonical writer value is the singular ``'call'`` (see
    ``src/roam/index/relations.py`` and every language extractor under
    ``src/roam/languages/*_lang.py``); plural ``'calls'`` is included
    defensively for plugin extractors. Pre-W493 the plural-only filter
    returned zero rows and silently made the inter-procedural DFS a no-op.
    """
    call_edges: dict[int, list[int]] = {}
    kind_ph = ", ".join("?" for _ in CALL_EDGE_KINDS)
    rows = conn.execute(
        f"SELECT source_id, target_id FROM edges WHERE kind IN ({kind_ph})",
        CALL_EDGE_KINDS,
    ).fetchall()
    for row in rows:
        call_edges.setdefault(row["source_id"], []).append(row["target_id"])
    return call_edges


def _findings_from_param_origin(
    origin_sym: int,
    callee_id: int,
    pidx: int,
    callee_summary: TaintSummary,
    summaries: dict[int, TaintSummary],
    chain_so_far: list[int],
    depth: int,
) -> list[TaintFinding]:
    """Findings emitted when a param-tainted argument flows through to a
    callee that has a sink at the same parameter index."""
    sinks_hit = callee_summary.param_to_sink.get(pidx, [])
    if not sinks_hit:
        return []
    origin_summary = summaries.get(origin_sym)
    source_type = "param"
    if origin_summary and origin_summary.direct_sources:
        source_type = origin_summary.direct_sources[0]
    return [
        TaintFinding(
            source_symbol_id=origin_sym,
            sink_symbol_id=callee_id,
            source_type=source_type,
            sink_type=sink,
            call_chain=[origin_sym] + chain_so_far,
            confidence=max(0.3, 0.9 - 0.1 * depth),
        )
        for sink in sinks_hit
    ]


def _findings_from_source_origin(
    origin_sym: int,
    callee_id: int,
    source_label: str,
    callee_summary: TaintSummary,
    chain_so_far: list[int],
    depth: int,
) -> list[TaintFinding]:
    """Findings emitted when a source-tainted value is passed to a callee
    that has any sink at any parameter."""
    out: list[TaintFinding] = []
    for _pidx, sinks_hit in callee_summary.param_to_sink.items():
        for sink in sinks_hit:
            out.append(
                TaintFinding(
                    source_symbol_id=origin_sym,
                    sink_symbol_id=callee_id,
                    source_type=source_label,
                    sink_type=sink,
                    call_chain=[origin_sym] + chain_so_far,
                    confidence=max(0.3, 0.9 - 0.1 * depth),
                )
            )
    return out


def _compute_returned_taint(
    callee_summary: TaintSummary,
    taint_origins: set[tuple[str, int | str]],
) -> set[tuple[str, int | str]]:
    """Decide what taint flows back through this callee's return value."""
    returned_taint: set[tuple[str, int | str]] = set()
    for origin in taint_origins:
        if origin[0] == "param":
            pidx = origin[1]
            if callee_summary.param_taints_return.get(pidx, False):
                returned_taint.update(taint_origins)
        elif origin[0] == "source" and callee_summary.return_from_source:
            returned_taint.add(origin)
    if callee_summary.return_from_source:
        for src in callee_summary.direct_sources:
            returned_taint.add(("source", src))
    return returned_taint


def _initial_taint_origins(summary: TaintSummary) -> set[tuple[str, int | str]]:
    """Seed taint origins for a starting symbol."""
    taint_origins: set[tuple[str, int | str]] = set()
    if summary.return_from_source:
        for src in summary.direct_sources:
            taint_origins.add(("source", src))
    for pidx in summary.param_taints_return:
        taint_origins.add(("param", pidx))
    return taint_origins


def _direct_findings(sym_id: int, summary: TaintSummary) -> list[TaintFinding]:
    """Source-to-sink within a single function — emit immediately, no
    propagation needed."""
    if not (summary.direct_sources and summary.direct_sinks):
        return []
    return [
        TaintFinding(
            source_symbol_id=sym_id,
            sink_symbol_id=sym_id,
            source_type=src,
            sink_type=snk,
            call_chain=[sym_id],
            confidence=0.9,
        )
        for src in summary.direct_sources
        for snk in summary.direct_sinks
    ]


def propagate_taint(
    conn,
    summaries: dict[int, TaintSummary],
    G,
    *,
    max_depth: int = 5,
) -> list[TaintFinding]:
    """Propagate taint across call-graph edges.

    For each call edge (caller -> callee):
    - If caller passes a tainted arg to callee and callee.param_to_sink[i]
      exists, record a cross-function finding.
    - If callee.param_taints_return[i], the call result inherits taint.
    - If callee is a sanitizer, taint dies.
    """
    call_edges = _build_call_adjacency(conn)
    findings: list[TaintFinding] = []
    visited_pairs: set[tuple[int, int, int]] = set()  # (origin, callee, depth)

    def _propagate(
        origin_sym: int,
        current_sym: int,
        taint_origins: set[tuple[str, int | str]],
        chain: list[int],
        depth: int,
    ):
        if depth > max_depth:
            return
        for callee_id in call_edges.get(current_sym, []):
            callee_summary = summaries.get(callee_id)
            if callee_summary is None or callee_summary.is_sanitizer:
                continue
            pair_key = (origin_sym, callee_id, depth)
            if pair_key in visited_pairs:
                continue
            visited_pairs.add(pair_key)
            new_chain = chain + [callee_id]

            for origin in taint_origins:
                if origin[0] == "param":
                    findings.extend(
                        _findings_from_param_origin(
                            origin_sym, callee_id, origin[1], callee_summary, summaries, new_chain, depth
                        )
                    )
                elif origin[0] == "source":
                    findings.extend(
                        _findings_from_source_origin(
                            origin_sym, callee_id, str(origin[1]), callee_summary, new_chain, depth
                        )
                    )

            returned_taint = _compute_returned_taint(callee_summary, taint_origins)
            if returned_taint:
                _propagate(origin_sym, callee_id, returned_taint, new_chain, depth + 1)

    for sym_id, summary in summaries.items():
        if summary.is_sanitizer:
            continue
        findings.extend(_direct_findings(sym_id, summary))
        taint_origins = _initial_taint_origins(summary)
        if taint_origins:
            _propagate(sym_id, sym_id, taint_origins, [], 0)

    return findings


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def store_taint_data(
    conn,
    summaries: dict[int, TaintSummary],
    findings: list[TaintFinding],
) -> None:
    """Persist taint summaries and findings to the database."""
    conn.execute("DELETE FROM taint_summaries")
    conn.execute("DELETE FROM taint_findings")

    summary_rows = []
    for sid, s in summaries.items():
        summary_rows.append(
            (
                sid,
                json.dumps({str(k): v for k, v in s.param_taints_return.items()}),
                json.dumps({str(k): v for k, v in s.param_to_sink.items()}),
                1 if s.return_from_source else 0,
                json.dumps(s.direct_sources),
                json.dumps(s.direct_sinks),
                1 if s.is_sanitizer else 0,
            )
        )
    if summary_rows:
        conn.executemany(
            """INSERT INTO taint_summaries
               (symbol_id, param_taints_return, param_to_sink,
                return_from_source, direct_sources, direct_sinks, is_sanitizer)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            summary_rows,
        )

    finding_rows = []
    for f in findings:
        finding_rows.append(
            (
                f.source_symbol_id,
                f.sink_symbol_id,
                f.source_type,
                f.sink_type,
                json.dumps(f.call_chain),
                len(f.call_chain),
                0,  # sanitized = False (only unsanitized are stored)
                f.confidence,
            )
        )
    if finding_rows:
        conn.executemany(
            """INSERT INTO taint_findings
               (source_symbol_id, sink_symbol_id, source_type, sink_type,
                call_chain, chain_length, sanitized, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            finding_rows,
        )


# ---------------------------------------------------------------------------
# Entry point (called from indexer)
# ---------------------------------------------------------------------------


def compute_and_store_taint(conn, root: Path, G=None, *, source_cache=None) -> None:
    """Full taint pipeline: intra-summaries, propagation, storage.

    Called from the indexer after graph construction.

    Args:
        source_cache: Optional ``{rel_path: (source_bytes, tree)}`` mapping
            populated upstream during Phase 2 (parse_extract). Forwarded to
            ``compute_all_summaries`` to eliminate Phase 5's redundant file
            I/O (W440). Defaults to ``None`` (backwards-compatible).
    """
    summaries = compute_all_summaries(conn, root, source_cache=source_cache)
    if not summaries:
        return

    findings = propagate_taint(conn, summaries, G)
    store_taint_data(conn, summaries, findings)
