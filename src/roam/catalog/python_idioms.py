"""Python-specific anti-pattern detectors.

Surfaced in v12.4 from the Python-pivot dogfood (2026-05-02). The
existing ``catalog/detectors.py`` covers language-agnostic algorithm
patterns (O(n²) string concat, sort-to-take, IO-in-loop). This module
adds Python-canonical anti-patterns that don't generalise to other
languages and would muddy the detector registry there.

Each detector returns a list of ``(symbol_id, pattern_id, severity,
description, fix_hint)`` tuples — same shape as the algorithm
detectors so they plug into the same ``roam math`` / ``roam smells``
plumbing.

Initial detectors (most-cited Python footguns):

1. **Mutable default argument** — ``def foo(x=[])`` / ``def bar(d={})``.
   The list/dict is created *once* at definition time and shared
   across calls. Classic source of "why does my list keep growing?".
2. **Bare except** — ``except:`` with no exception type. Catches
   ``SystemExit`` and ``KeyboardInterrupt``, masking critical
   shutdown signals. PEP 8 explicitly discourages this.
3. **Comparison to None with ``==``** — should use ``is None``.
   Not just style: ``__eq__`` overrides can do anything.
4. **f-string in logger calls** — ``logger.info(f"x={x}")`` evaluates
   the format string even if the log level is below INFO. Use
   ``logger.info("x=%s", x)`` for lazy evaluation.

Detectors are line-anchored regex over the source text rather than
AST queries because Python's tree-sitter grammar exposes default
argument values as a deeply-nested chain that's brittle to query.
The regex approach is "good enough" for these specific patterns and
ports trivially to YAML/Jupyter notebooks if we extend later.
"""

from __future__ import annotations

import re
import sqlite3

# ---------------------------------------------------------------------------
# Pattern regexes (compiled once)
# ---------------------------------------------------------------------------

# def foo(x=[]) — also matches dict, set literal, function calls like list()
# Skips ``=None`` and immutable literals (``=0``, ``=""``, ``=()``).
_MUTABLE_DEFAULT_RE = re.compile(
    r"def\s+\w+\s*\([^)]*?(\w+)\s*=\s*"
    r"(\[\s*\]|\{\s*\}|\{\s*[^}:]+\s*\}|list\(\s*\)|dict\(\s*\)|set\(\s*\))",
)

# bare except — matches ``except:`` with optional whitespace,
# but NOT ``except SomeError:`` or ``except (A, B):``.
_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:", re.MULTILINE)

# == None or != None at end-of-expression positions. The leading
# ``\b`` doesn't apply (operators aren't word chars); we anchor on
# the comparison operator and require ``None`` followed by a non-word
# character to avoid matching ``Nonetype`` etc.
_NONE_EQ_RE = re.compile(r"(==|!=)\s*None\b")

# logger.<level>(f"..."): logger / log / logging variants
_LOGGER_FSTRING_RE = re.compile(
    r"\b(?:logger|log|logging|self\.logger|self\.log)\.(?:debug|info|warning|warn|error|critical|exception)\s*\(\s*f[\"']",
)


def _file_text(conn: sqlite3.Connection, file_id: int) -> str | None:
    """Read the source text of a file via roam.index — but the index
    doesn't store source. Instead we read from disk via the file path.
    Fast (mmap) and safe to no-op on read errors.
    """
    row = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        return None
    path = row[0]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _python_files(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return ``(file_id, path)`` for every Python file in the index."""
    return [(int(r[0]), r[1]) for r in conn.execute("SELECT id, path FROM files WHERE language = 'python'").fetchall()]


def _line_to_symbol(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, int, int, str]]:
    """``(symbol_id, line_start, line_end, name)`` for symbols in
    ``file_id`` ordered by line. Used to attribute regex matches to
    the enclosing symbol."""
    return [
        (int(r[0]), int(r[1] or 0), int(r[2] or 0), r[3])
        for r in conn.execute(
            "SELECT id, line_start, line_end, name FROM symbols WHERE file_id = ? ORDER BY line_start",
            (file_id,),
        ).fetchall()
    ]


def _enclosing_symbol(line_no: int, sym_index: list[tuple[int, int, int, str]]) -> tuple[int, str] | None:
    """Return the innermost ``(symbol_id, name)`` whose line range
    contains ``line_no``, or ``None``. Innermost = max line_start."""
    best: tuple[int, str] | None = None
    best_start = -1
    for sid, start, end, name in sym_index:
        if start <= line_no <= end and start > best_start:
            best = (sid, name)
            best_start = start
    return best


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _idiom_finding(
    *,
    task_id: str,
    detected_way: str,
    symbol_id: int,
    symbol_name: str,
    file_path: str,
    line_no: int,
    reason: str,
    confidence: str = "high",
    fix: str | None = None,
) -> dict:
    """Build a finding dict in the shape ``catalog.detectors._finding``
    produces, so the same downstream calibration / display works."""
    return {
        "task_id": task_id,
        "detected_way": detected_way,
        "suggested_way": detected_way,
        "symbol_id": symbol_id,
        "symbol_name": symbol_name,
        "kind": "function",
        "location": f"{file_path}:{line_no}",
        "confidence": confidence,
        "reason": reason,
        "fix": fix or "",
    }


def detect_mutable_default_arg(conn: sqlite3.Connection) -> list[dict]:
    """Find ``def foo(x=[])`` / ``def foo(d={})`` patterns."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _MUTABLE_DEFAULT_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            param = match.group(1)
            findings.append(
                _idiom_finding(
                    task_id="py-mutable-default-arg",
                    detected_way="default-mutable",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=(f"Mutable default arg: ``{param}={match.group(2)}`` is shared across calls"),
                    confidence="high",
                    fix=f"def fn({param}=None): ...; if {param} is None: {param} = [] / {{}} / set()",
                )
            )
    return findings


def detect_bare_except(conn: sqlite3.Connection) -> list[dict]:
    """Find ``except:`` (no exception type)."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _BARE_EXCEPT_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-bare-except",
                    detected_way="catch-all",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="bare ``except:`` catches SystemExit/KeyboardInterrupt — shutdown signals masked",
                    confidence="high",
                    fix="except Exception:  # or the specific class you mean to handle",
                )
            )
    return findings


def detect_none_eq(conn: sqlite3.Connection) -> list[dict]:
    """Find ``x == None`` / ``x != None``."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _NONE_EQ_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            op = match.group(1)
            replacement = "is" if op == "==" else "is not"
            findings.append(
                _idiom_finding(
                    task_id="py-none-eq",
                    detected_way="eq-not-is",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=f"``{op} None`` invokes ``__eq__``; use ``{replacement} None`` (idiomatic, faster)",
                    confidence="medium",
                    fix=f"x {replacement} None",
                )
            )
    return findings


def detect_logger_fstring(conn: sqlite3.Connection) -> list[dict]:
    """Find ``logger.info(f"...")`` — eager-format anti-pattern."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _LOGGER_FSTRING_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-logger-fstring",
                    detected_way="eager-format",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="f-string in logger call evaluates even when level discards the message",
                    confidence="high",
                    fix='logger.info("x=%s", x)',
                )
            )
    return findings


# Detector registry — same shape ``cmd_math`` expects from ``_MATH_DETECTORS``
# (task_id, pattern_id, detect_fn). Re-exported so registration is one
# import line elsewhere.
PYTHON_IDIOM_DETECTORS = [
    ("py-mutable-default-arg", "default-mutable", detect_mutable_default_arg),
    ("py-bare-except", "catch-all", detect_bare_except),
    ("py-none-eq", "eq-not-is", detect_none_eq),
    ("py-logger-fstring", "eager-format", detect_logger_fstring),
]
