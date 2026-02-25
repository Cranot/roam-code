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


def _read_body_lines(conn, symbol_id: int, project_root: Path) -> tuple[list[str], str | None, int]:
    """Read body lines for a symbol from disk.

    Returns ``(body_lines, signature, line_start)`` where *body_lines*
    excludes the declaration line itself.
    """
    row = conn.execute(
        """
        SELECT s.line_start, s.line_end, s.signature,
               f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.id = ?
        """,
        (symbol_id,),
    ).fetchone()
    if row is None:
        return [], None, 0

    ls = row["line_start"] or 1
    le = row["line_end"] or ls
    rel_path = row["file_path"] or ""
    full_path = project_root / rel_path
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [], row["signature"], ls

    all_lines = text.splitlines()
    # body_lines: everything after declaration line
    body = all_lines[ls : min(le, len(all_lines))]
    return body, row["signature"], ls


# ---------------------------------------------------------------------------
# Intra-procedural variable taint tracking
# ---------------------------------------------------------------------------


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
    tainted_vars: dict[str, set[tuple[str, int | str]]] = {}

    # Initialise params as tainted
    for idx, pname in enumerate(param_names):
        tainted_vars[pname] = {("param", idx)}

    param_taints_return: dict[int, bool] = {}
    param_to_sink: dict[int, list[str]] = {}
    return_from_source = False
    found_sources: list[str] = []
    found_sinks: list[str] = []

    for raw_line in body_lines:
        line = raw_line.strip()
        line_lower = line.lower()

        # ---- Check for sanitizer calls clearing taint ----
        is_sanitize_line = False
        for sname in sanitizer_names:
            if sname in line_lower:
                is_sanitize_line = True
                break

        # ---- Check for assignment ----
        assigned_var: str | None = None
        for pat in _ASSIGN_PATTERNS:
            m = pat.search(line)
            if m:
                assigned_var = m.group(1).strip()
                break

        # ---- Determine taint of the right-hand side ----
        rhs = line
        if assigned_var and "=" in rhs:
            eq_idx = rhs.index("=")
            rhs = rhs[eq_idx + 1 :]

        rhs_taint: set[tuple[str, int | str]] = set()

        # Check if RHS references a source
        for src in sources:
            if src.lower() in rhs.lower():
                rhs_taint.add(("source", src))
                if src not in found_sources:
                    found_sources.append(src)
                break

        # Check if RHS references tainted variables
        rhs_idents = set(_IDENT_RE.findall(rhs))
        for ident in rhs_idents:
            if ident in tainted_vars:
                rhs_taint.update(tainted_vars[ident])

        # If this is a sanitizer call, kill the taint
        if is_sanitize_line and assigned_var:
            tainted_vars.pop(assigned_var, None)
        elif assigned_var and rhs_taint:
            tainted_vars[assigned_var] = rhs_taint
        elif assigned_var:
            # Clean assignment — clear any previous taint
            tainted_vars.pop(assigned_var, None)

        # ---- Check return statements ----
        if line.startswith("return ") or line == "return":
            return_idents = set(_IDENT_RE.findall(line[6:]))
            for ident in return_idents:
                if ident in tainted_vars:
                    for origin in tainted_vars[ident]:
                        if origin[0] == "param":
                            param_taints_return[origin[1]] = True
                        elif origin[0] == "source":
                            return_from_source = True

        # ---- Check sink calls ----
        for sink in sinks:
            if sink.lower() in line_lower:
                if sink not in found_sinks:
                    found_sinks.append(sink)
                # Find tainted args flowing into this sink
                sink_idents = set(_IDENT_RE.findall(line))
                for ident in sink_idents:
                    if ident in tainted_vars:
                        for origin in tainted_vars[ident]:
                            if origin[0] == "param":
                                pidx = origin[1]
                                if pidx not in param_to_sink:
                                    param_to_sink[pidx] = []
                                if sink not in param_to_sink[pidx]:
                                    param_to_sink[pidx].append(sink)
                break

    return {
        "param_taints_return": param_taints_return,
        "param_to_sink": param_to_sink,
        "return_from_source": return_from_source,
        "direct_sources": found_sources,
        "direct_sinks": found_sinks,
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
) -> dict[int, TaintSummary]:
    """Compute intra-procedural taint summaries for all functions/methods."""
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
    # Build adjacency from edges table for call edges
    call_edges: dict[int, list[int]] = {}
    rows = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'calls'").fetchall()
    for row in rows:
        src = row["source_id"]
        tgt = row["target_id"]
        call_edges.setdefault(src, []).append(tgt)

    findings: list[TaintFinding] = []
    visited_pairs: set[tuple[int, int, int]] = set()  # (source_sym, sink_sym, depth)

    def _propagate(
        origin_sym: int,
        current_sym: int,
        taint_origins: set[tuple[str, int | str]],
        chain: list[int],
        depth: int,
    ):
        if depth > max_depth:
            return

        callees = call_edges.get(current_sym, [])
        for callee_id in callees:
            callee_summary = summaries.get(callee_id)
            if callee_summary is None:
                continue

            # If callee is a sanitizer, taint dies
            if callee_summary.is_sanitizer:
                continue

            new_chain = chain + [callee_id]
            pair_key = (origin_sym, callee_id, depth)
            if pair_key in visited_pairs:
                continue
            visited_pairs.add(pair_key)

            # Check if any taint origin is a param that flows to a sink
            # in the callee (simplified: we assume arg position matches
            # the taint origin param index for direct calls)
            for origin in taint_origins:
                if origin[0] == "param":
                    pidx = origin[1]
                    sinks_hit = callee_summary.param_to_sink.get(pidx, [])
                    for sink in sinks_hit:
                        # Get source info from the origin symbol
                        origin_summary = summaries.get(origin_sym)
                        source_type = "param"
                        if origin_summary and origin_summary.direct_sources:
                            source_type = origin_summary.direct_sources[0]
                        findings.append(
                            TaintFinding(
                                source_symbol_id=origin_sym,
                                sink_symbol_id=callee_id,
                                source_type=source_type,
                                sink_type=sink,
                                call_chain=[origin_sym] + new_chain,
                                confidence=max(0.3, 0.9 - 0.1 * depth),
                            )
                        )
                elif origin[0] == "source":
                    # A source-tainted value is passed to callee
                    for pidx, sinks_hit in callee_summary.param_to_sink.items():
                        for sink in sinks_hit:
                            findings.append(
                                TaintFinding(
                                    source_symbol_id=origin_sym,
                                    sink_symbol_id=callee_id,
                                    source_type=str(origin[1]),
                                    sink_type=sink,
                                    call_chain=[origin_sym] + new_chain,
                                    confidence=max(0.3, 0.9 - 0.1 * depth),
                                )
                            )

            # Propagate through callee if it returns tainted data
            returned_taint: set[tuple[str, int | str]] = set()
            for origin in taint_origins:
                if origin[0] == "param":
                    pidx = origin[1]
                    if callee_summary.param_taints_return.get(pidx, False):
                        returned_taint.update(taint_origins)
                elif origin[0] == "source":
                    if callee_summary.return_from_source:
                        returned_taint.add(origin)

            if callee_summary.return_from_source:
                for src in callee_summary.direct_sources:
                    returned_taint.add(("source", src))

            if returned_taint:
                _propagate(origin_sym, callee_id, returned_taint, new_chain, depth + 1)

    # Start propagation from symbols that have source-tainted data
    for sym_id, summary in summaries.items():
        if summary.is_sanitizer:
            continue

        taint_origins: set[tuple[str, int | str]] = set()

        # Source-tainted returns: the function itself produces tainted data
        if summary.return_from_source:
            for src in summary.direct_sources:
                taint_origins.add(("source", src))

        # Params that flow to sinks in callees
        for pidx in summary.param_taints_return:
            taint_origins.add(("param", pidx))

        # Direct source-to-sink within the function: record as finding
        if summary.direct_sources and summary.direct_sinks:
            for src in summary.direct_sources:
                for snk in summary.direct_sinks:
                    findings.append(
                        TaintFinding(
                            source_symbol_id=sym_id,
                            sink_symbol_id=sym_id,
                            source_type=src,
                            sink_type=snk,
                            call_chain=[sym_id],
                            confidence=0.9,
                        )
                    )

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


def compute_and_store_taint(conn, root: Path, G=None) -> None:
    """Full taint pipeline: intra-summaries, propagation, storage.

    Called from the indexer after graph construction.
    """
    summaries = compute_all_summaries(conn, root)
    if not summaries:
        return

    findings = propagate_taint(conn, summaries, G)
    store_taint_data(conn, summaries, findings)
