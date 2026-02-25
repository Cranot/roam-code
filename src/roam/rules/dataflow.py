"""Intra- and inter-procedural dataflow heuristics.

This module implements a lightweight, deterministic subset:
- dead assignments (assigned variable never read in the same function)
- unused parameters (parameter never read in function body)
- source-to-sink flow in the same function (string-pattern based)
- inter_source_to_sink (cross-function taint from taint analysis tables)
- inter_unused_param (params with no dataflow effect per taint summaries)
- inter_unused_return (return values not consumed by callers)

It is designed for fast, index-backed scans and custom `dataflow_match` rules.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from pathlib import Path

from roam.db.connection import find_project_root

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")

_ASSIGN_PATTERNS = (
    re.compile(r"^\s*(?:let|const|var)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)"),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[\+\-\*/%]="),
)

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
)

_IGNORED_NAMES = {"_", "self", "cls"}


def _match_glob(path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    norm = (path or "").replace("\\", "/")
    pat = pattern.replace("\\", "/")
    if fnmatch.fnmatch(norm, pat):
        return True
    return Path(norm).match(pat)


def _is_test_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    return "/tests/" in p or "/test/" in p or p.endswith("_test.py") or p.endswith(".spec.js") or p.endswith(".spec.ts")


def _parse_param_names(signature: str | None) -> list[str]:
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
        if token and token not in _IGNORED_NAMES:
            names.append(token)
    return names


def _read_file_lines(project_root: Path, rel_path: str) -> list[str]:
    path = project_root / rel_path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return text.splitlines()


def _extract_assignments(body_lines: list[str], start_line: int) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for idx, line in enumerate(body_lines):
        # Strip trailing comments for basic signal quality.
        line_no_comments = line.split("#", 1)[0].split("//", 1)[0]
        for pattern in _ASSIGN_PATTERNS:
            m = pattern.search(line_no_comments)
            if not m:
                continue
            name = m.group(1).strip()
            if not name or name in _IGNORED_NAMES:
                continue
            out.append((name, start_line + idx))
            break
    return out


def _token_counts(text: str) -> Counter:
    return Counter(match.group(1) for match in _IDENT_RE.finditer(text))


def _find_source_sink(
    body_lines: list[str],
    start_line: int,
    *,
    sources: tuple[str, ...],
    sinks: tuple[str, ...],
) -> tuple[int, str, int, str] | None:
    source_hit: tuple[int, str] | None = None
    sink_hit: tuple[int, str] | None = None

    for idx, raw in enumerate(body_lines):
        line = raw.lower()
        line_no = start_line + idx
        if source_hit is None:
            for src in sources:
                if src.lower() in line:
                    source_hit = (line_no, src)
                    break
        for sink in sinks:
            if sink.lower() in line:
                sink_hit = (line_no, sink)
                break
        if source_hit and sink_hit and source_hit[0] <= sink_hit[0]:
            return source_hit[0], source_hit[1], sink_hit[0], sink_hit[1]
    return None


_ALL_PATTERNS = frozenset(
    {
        "dead_assignment",
        "unused_param",
        "source_to_sink",
        "inter_source_to_sink",
        "inter_unused_param",
        "inter_unused_return",
    }
)

_INTRA_DEFAULTS = frozenset({"dead_assignment", "unused_param", "source_to_sink"})

_INTER_PATTERNS = frozenset({"inter_source_to_sink", "inter_unused_param", "inter_unused_return"})


def _normalize_patterns(patterns: list[str] | tuple[str, ...] | str | None) -> set[str]:
    if patterns is None:
        return set(_INTRA_DEFAULTS)
    if isinstance(patterns, str):
        values = [patterns]
    else:
        values = list(patterns)
    out = set()
    for value in values:
        key = str(value).strip().lower()
        if key in _ALL_PATTERNS:
            out.add(key)
    return out


def _collect_inter_findings(
    conn,
    *,
    patterns: set[str],
    file_glob: str | None = None,
    max_matches: int = 0,
) -> list[dict]:
    """Collect inter-procedural dataflow findings from taint analysis tables."""
    findings: list[dict] = []

    # Check if taint tables exist
    try:
        conn.execute("SELECT 1 FROM taint_findings LIMIT 0")
    except Exception:
        return []

    if "inter_source_to_sink" in patterns:
        rows = conn.execute(
            """
            SELECT tf.source_type, tf.sink_type, tf.call_chain,
                   tf.chain_length, tf.confidence,
                   s1.name AS src_name, COALESCE(s1.qualified_name, s1.name) AS src_qname,
                   f1.path AS src_file, s1.line_start AS src_line,
                   s2.name AS sink_name, COALESCE(s2.qualified_name, s2.name) AS sink_qname,
                   f2.path AS sink_file, s2.line_start AS sink_line
            FROM taint_findings tf
            JOIN symbols s1 ON tf.source_symbol_id = s1.id
            JOIN files f1 ON s1.file_id = f1.id
            JOIN symbols s2 ON tf.sink_symbol_id = s2.id
            JOIN files f2 ON s2.file_id = f2.id
            WHERE tf.sanitized = 0
            ORDER BY tf.confidence DESC
            """
        ).fetchall()
        for row in rows:
            src_file = (row["src_file"] or "").replace("\\", "/")
            sink_file = (row["sink_file"] or "").replace("\\", "/")
            if file_glob and not _match_glob(src_file, file_glob) and not _match_glob(sink_file, file_glob):
                continue
            findings.append(
                {
                    "type": "inter_source_to_sink",
                    "symbol": row["src_qname"],
                    "file": src_file,
                    "line": row["src_line"],
                    "sink_symbol": row["sink_qname"],
                    "sink_file": sink_file,
                    "sink_line": row["sink_line"],
                    "source": row["source_type"],
                    "sink": row["sink_type"],
                    "chain_length": row["chain_length"],
                    "confidence": row["confidence"],
                    "reason": (
                        "cross-function taint: {} in {} -> {} in {} (depth {})".format(
                            row["source_type"],
                            row["src_qname"],
                            row["sink_type"],
                            row["sink_qname"],
                            row["chain_length"],
                        )
                    ),
                }
            )

    if "inter_unused_param" in patterns:
        try:
            conn.execute("SELECT 1 FROM taint_summaries LIMIT 0")
        except Exception:
            pass
        else:
            rows = conn.execute(
                """
                SELECT ts.symbol_id, ts.param_taints_return, ts.param_to_sink,
                       s.name, COALESCE(s.qualified_name, s.name) AS qname,
                       s.signature, f.path AS file_path, s.line_start
                FROM taint_summaries ts
                JOIN symbols s ON ts.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE ts.is_sanitizer = 0
                """
            ).fetchall()
            for row in rows:
                fp = (row["file_path"] or "").replace("\\", "/")
                if file_glob and not _match_glob(fp, file_glob):
                    continue
                try:
                    ptr = json.loads(row["param_taints_return"] or "{}")
                    pts = json.loads(row["param_to_sink"] or "{}")
                except Exception:
                    continue
                params = _parse_param_names(row["signature"])
                for idx, pname in enumerate(params):
                    sidx = str(idx)
                    if not ptr.get(sidx, False) and not pts.get(sidx):
                        findings.append(
                            {
                                "type": "inter_unused_param",
                                "symbol": row["qname"],
                                "file": fp,
                                "line": row["line_start"],
                                "variable": pname,
                                "reason": (
                                    "parameter '{}' in {} has no dataflow effect "
                                    "(not in return, not in sink)".format(pname, row["qname"])
                                ),
                            }
                        )

    if "inter_unused_return" in patterns:
        try:
            conn.execute("SELECT 1 FROM taint_summaries LIMIT 0")
            conn.execute("SELECT 1 FROM symbol_metrics LIMIT 0")
        except Exception:
            pass
        else:
            rows = conn.execute(
                """
                SELECT ts.symbol_id, s.name, COALESCE(s.qualified_name, s.name) AS qname,
                       f.path AS file_path, s.line_start
                FROM taint_summaries ts
                JOIN symbols s ON ts.symbol_id = s.id
                JOIN files f ON s.file_id = f.id
                JOIN symbol_metrics sm ON s.id = sm.symbol_id
                WHERE sm.return_count > 0
                  AND ts.return_from_source = 0
                  AND ts.is_sanitizer = 0
                """
            ).fetchall()
            for row in rows:
                fp = (row["file_path"] or "").replace("\\", "/")
                if file_glob and not _match_glob(fp, file_glob):
                    continue
                caller_count = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE target_id = ? AND kind = 'calls'",
                    (row["symbol_id"],),
                ).fetchone()[0]
                if caller_count > 0:
                    findings.append(
                        {
                            "type": "inter_unused_return",
                            "symbol": row["qname"],
                            "file": fp,
                            "line": row["line_start"],
                            "reason": (
                                "return value of {} is computed but may not be used by callers".format(
                                    row["qname"]
                                )
                            ),
                        }
                    )

    if max_matches > 0:
        findings = findings[:max_matches]
    return findings


def collect_dataflow_findings(
    conn,
    *,
    patterns: list[str] | tuple[str, ...] | str | None = None,
    file_glob: str | None = None,
    max_matches: int = 0,
    sources: list[str] | tuple[str, ...] | None = None,
    sinks: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Collect intra- and inter-procedural dataflow findings."""
    target_patterns = _normalize_patterns(patterns)
    if not target_patterns:
        return []

    # Collect inter-procedural findings from taint tables
    inter_patterns = target_patterns & _INTER_PATTERNS
    inter_findings: list[dict] = []
    if inter_patterns:
        inter_findings = _collect_inter_findings(
            conn,
            patterns=inter_patterns,
            file_glob=file_glob,
            max_matches=max_matches,
        )

    # If only inter-procedural patterns requested, return early
    intra_patterns = target_patterns - _INTER_PATTERNS
    if not intra_patterns:
        inter_findings.sort(key=lambda f: (f["file"], int(f.get("line") or 0), f["type"], f.get("symbol") or ""))
        if max_matches > 0:
            return inter_findings[:max_matches]
        return inter_findings

    source_patterns = tuple(sources) if sources else _DEFAULT_SOURCES
    sink_patterns = tuple(sinks) if sinks else _DEFAULT_SINKS

    rows = conn.execute(
        """
        SELECT
            s.id,
            s.name,
            COALESCE(s.qualified_name, s.name) AS qualified_name,
            s.signature,
            s.kind,
            s.line_start,
            s.line_end,
            f.path AS file_path
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ('function', 'method')
          AND s.line_start IS NOT NULL
          AND s.line_end IS NOT NULL
          AND s.line_end >= s.line_start
        ORDER BY f.path, s.line_start
        """
    ).fetchall()

    project_root = find_project_root()
    file_cache: dict[str, list[str]] = {}
    findings: list[dict] = []

    for row in rows:
        file_path = (row["file_path"] or "").replace("\\", "/")
        if not file_path or _is_test_path(file_path):
            continue
        if not _match_glob(file_path, file_glob):
            continue

        lines = file_cache.get(file_path)
        if lines is None:
            lines = _read_file_lines(project_root, file_path)
            file_cache[file_path] = lines
        if not lines:
            continue

        line_start = int(row["line_start"] or 1)
        line_end = int(row["line_end"] or line_start)
        if line_start < 1:
            line_start = 1
        if line_end < line_start:
            line_end = line_start
        if line_start > len(lines):
            continue

        # Scope slice and body slice (excluding declaration line).
        scope_lines = lines[line_start - 1 : min(line_end, len(lines))]
        body_lines = lines[line_start : min(line_end, len(lines))]
        body_text = "\n".join(body_lines)
        token_counts = _token_counts(body_text)

        symbol_name = row["qualified_name"] or row["name"]

        if "dead_assignment" in target_patterns and body_lines:
            seen_vars: set[str] = set()
            assignments = _extract_assignments(body_lines, line_start + 1)
            for var_name, var_line in assignments:
                if var_name in seen_vars:
                    continue
                seen_vars.add(var_name)
                if token_counts.get(var_name, 0) <= 1:
                    findings.append(
                        {
                            "type": "dead_assignment",
                            "symbol": symbol_name,
                            "file": file_path,
                            "line": var_line,
                            "variable": var_name,
                            "reason": (f"'{var_name}' is assigned but never read in {symbol_name}"),
                        }
                    )

        if "unused_param" in target_patterns and body_lines:
            params = _parse_param_names(row["signature"])
            for name in params:
                if name in _IGNORED_NAMES or name.startswith("_"):
                    continue
                if token_counts.get(name, 0) <= 0:
                    findings.append(
                        {
                            "type": "unused_param",
                            "symbol": symbol_name,
                            "file": file_path,
                            "line": line_start,
                            "variable": name,
                            "reason": (f"parameter '{name}' is never read in {symbol_name}"),
                        }
                    )

        if "source_to_sink" in target_patterns and body_lines:
            hit = _find_source_sink(
                body_lines,
                line_start + 1,
                sources=source_patterns,
                sinks=sink_patterns,
            )
            if hit is not None:
                src_line, source, sink_line, sink = hit
                findings.append(
                    {
                        "type": "source_to_sink",
                        "symbol": symbol_name,
                        "file": file_path,
                        "line": sink_line,
                        "source_line": src_line,
                        "source": source,
                        "sink": sink,
                        "reason": (f"possible source-to-sink flow in {symbol_name}: '{source}' -> '{sink}'"),
                    }
                )

        if max_matches > 0 and len(findings) >= max_matches:
            break

    # Merge inter-procedural findings
    findings.extend(inter_findings)

    findings.sort(key=lambda f: (f["file"], int(f.get("line") or 0), f["type"], f.get("symbol") or ""))
    if max_matches > 0:
        return findings[:max_matches]
    return findings
