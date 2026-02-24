"""Basic intra-procedural dataflow heuristics.

This module intentionally implements a lightweight, deterministic subset:
- dead assignments (assigned variable never read in the same function)
- unused parameters (parameter never read in function body)
- source-to-sink flow in the same function (string-pattern based)

It is designed for fast, index-backed scans and custom `dataflow_match` rules.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import fnmatch
import re

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
    return (
        "/tests/" in p
        or "/test/" in p
        or p.endswith("_test.py")
        or p.endswith(".spec.js")
        or p.endswith(".spec.ts")
    )


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


def _normalize_patterns(patterns: list[str] | tuple[str, ...] | str | None) -> set[str]:
    if patterns is None:
        return {"dead_assignment", "unused_param", "source_to_sink"}
    if isinstance(patterns, str):
        values = [patterns]
    else:
        values = list(patterns)
    out = set()
    for value in values:
        key = str(value).strip().lower()
        if key in {"dead_assignment", "unused_param", "source_to_sink"}:
            out.add(key)
    return out


def collect_dataflow_findings(
    conn,
    *,
    patterns: list[str] | tuple[str, ...] | str | None = None,
    file_glob: str | None = None,
    max_matches: int = 0,
    sources: list[str] | tuple[str, ...] | None = None,
    sinks: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Collect intra-procedural dataflow findings from indexed functions/methods."""
    target_patterns = _normalize_patterns(patterns)
    if not target_patterns:
        return []

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
                            "reason": (
                                f"'{var_name}' is assigned but never read "
                                f"in {symbol_name}"
                            ),
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
                            "reason": (
                                f"parameter '{name}' is never read "
                                f"in {symbol_name}"
                            ),
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
                        "reason": (
                            f"possible source-to-sink flow in {symbol_name}: "
                            f"'{source}' -> '{sink}'"
                        ),
                    }
                )

        if max_matches > 0 and len(findings) >= max_matches:
            break

    findings.sort(key=lambda f: (f["file"], int(f.get("line") or 0), f["type"], f.get("symbol") or ""))
    if max_matches > 0:
        return findings[:max_matches]
    return findings
