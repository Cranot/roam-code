"""Python-specific anti-pattern detectors.

The
existing ``src/roam/catalog/detectors.py`` covers language-agnostic algorithm
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

from roam.db.edge_kinds import CALL_EDGE_KINDS

__all__ = [
    # Detector registry (consumed by detectors._iter_registered_detectors).
    "PYTHON_IDIOM_DETECTORS",
    # Helpers used by external command modules (e.g. cmd_context).
    "has_decorator",
    "fixture_kind",
    "is_model_class",
    # Detector functions (alphabetical).
    "detect_async_not_awaited",
    "detect_async_with_missing",
    "detect_bare_except",
    "detect_broad_except",
    "detect_dict_keys_iter",
    "detect_django_n1",
    "detect_except_pass",
    "detect_fastapi_depends",
    "detect_flask_debug_true",
    "detect_flask_routes",
    "detect_flask_secret_key_literal",
    "detect_lambda_in_loop",
    "detect_lock_without_with",
    "detect_logger_fstring",
    "detect_mutable_default_arg",
    "detect_none_eq",
    "detect_open_without_with",
    "detect_pandas_iterrows",
    "detect_sqlalchemy_lazy",
    "detect_star_import",
    "detect_sync_calls_async_via_graph",
    "detect_sync_in_async",
    "detect_type_eq",
]

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

# Sync-IO-in-async: blocking calls inside ``async def`` bodies that
# starve the event loop. Each entry is (regex, suggested replacement).
# Detector iterates over async functions only (via is_async column).
_SYNC_IN_ASYNC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\brequests\.(?:get|post|put|delete|patch|head|options|request)\("),
        "use httpx.AsyncClient or aiohttp",
    ),
    (re.compile(r"\btime\.sleep\("), "use ``await asyncio.sleep(...)``"),
    (re.compile(r"\burllib\.request\.urlopen\("), "use httpx.AsyncClient or aiohttp"),
    (
        re.compile(r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\("),
        "use ``await asyncio.create_subprocess_exec/_shell``",
    ),
    (re.compile(r"\bsocket\.(?:recv|send|sendall|recvfrom|sendto)\("), "use asyncio streams (open_connection, etc.)"),
    (re.compile(r"\bpsycopg2\.connect\("), "use asyncpg.connect"),
    # ``open(...)`` in async without ``async with`` — pattern-only match
    # below (open without enclosing ``async with``) requires AST. Skip
    # at the regex stage.
]

# ``open(...)`` without ``with`` enclosing — file resource leak.
# Match any ``open(`` call; the caller already strips lines containing
# ``with `` / ``async with`` so we only see uses outside those. Skip
# ``os.open`` (low-level descriptor) and ``codecs.open`` (legitimately
# different lifecycle in some legacy code).
_OPEN_WITHOUT_WITH_RE = re.compile(
    r"(?<!\.)\bopen\s*\(",
)

# ``from X import *`` — pollutes the namespace, masks shadowing,
# breaks ``mypy --strict``.
_STAR_IMPORT_RE = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\*", re.MULTILINE)

# ``for k in d.keys():`` — redundant, slower than ``for k in d``
_DICT_KEYS_ITER_RE = re.compile(r"\bfor\s+\w+\s+in\s+\w+\.keys\(\s*\)\s*:")

# ``"x = {}".format(x)`` / ``"x = %s" % x`` when an f-string is
# preferred (PEP 498 / Python 3.6+).
_OLD_FORMAT_RE = re.compile(r"[\"']\s*\.format\s*\(")

# ``async with`` missing on aiofiles / httpx.AsyncClient — async
# resource leak. ``aiofiles.open(...)`` and ``httpx.AsyncClient()``
# return async context managers and must be entered with
# ``async with``. A bare assignment ``f = aiofiles.open(...)`` leaks.
_ASYNC_OPEN_RE = re.compile(r"\baiofiles\.open\s*\(")
_HTTPX_CLIENT_RE = re.compile(r"\bhttpx\.AsyncClient\s*\(")

# Comparing types with ``type(x) == X`` instead of ``isinstance``.
# Misses subclasses + obscures the intent.
_TYPE_EQ_RE = re.compile(r"\btype\(\w+\)\s*==\s*\w+")

# ``DataFrame.iterrows()`` yields Series rows, erases dtype consistency, and
# is slower than ``itertuples()`` for row-wise iteration. Prefer vectorized
# pandas operations when possible; ``itertuples()`` is the conservative local
# rewrite when a row loop is truly needed.
_PANDAS_ITERROWS_RE = re.compile(r"\.iterrows\s*\(")

# ``isinstance(x, int)`` matches True/False (bool is a subclass of int).
# Pattern: ``isinstance(VAR, int)`` without prior ``not isinstance(VAR, bool)``.
# Detector below uses regex + per-line context check (caller pre-strips
# strings).
_ISINSTANCE_INT_RE = re.compile(r"\bisinstance\s*\(\s*(\w+)\s*,\s*int\s*\)")

# ``__all__ = [...]`` — module-level export list.
_ALL_DECLARATION_RE = re.compile(r"^__all__\s*=\s*[\[(]([^\])]*)[\])]", re.MULTILINE)

# Threading.Lock without ``with`` — risk of unreleased lock on exception.
_LOCK_ACQUIRE_RE = re.compile(r"\b(\w+)\.acquire\s*\(\s*\)")

# Magic numbers — literal numeric constants in code (excluding 0, 1, -1,
# typical loop bounds). Conservative: only flag floats > 1.0 and ints
# > 100 since most common magic-number false positives are loop indexes.
# Caller skips lines containing ``=`` at start (constant declaration is
# legitimate).
_MAGIC_NUMBER_RE = re.compile(
    r"(?<![\w.])(?:[2-9][0-9]{2,}|\d+\.\d+)\b",
)

# Recursion: ``def fn(...): ... fn(...)`` — detected by checking if the
# function name appears in its own body. Done in detector loop, not
# regex.

# Django ORM N+1: ``.filter(...)`` then ``for x in qs:`` then access
# of related field (e.g. ``x.author.name``). The simplified detector
# below catches the canonical pattern: ``.all()`` immediately
# followed by ``for x in qs`` in the same function body.
_DJANGO_ALL_THEN_FOR = re.compile(
    r"\.\s*all\s*\(\s*\)[^\n]*\n[^\n]*for\s+\w+\s+in\s+\w+\s*:",
)
# Calling ``.objects.filter(...)`` inside a loop — N+1.
_DJANGO_FILTER_IN_LOOP = re.compile(
    r"^\s+.*\.objects\.\s*(?:filter|get)\s*\(",
    re.MULTILINE,
)

# SQLAlchemy: ``.all()`` then iterate accessing a relationship attribute.
# Simplified pattern: ``.all()`` in any function with ``relationship``
# imported. Caller needs to verify it's worth flagging.
_SQLALCHEMY_RELATIONSHIP = re.compile(r"=\s*relationship\s*\(")
_SQLALCHEMY_ALL_THEN_DOT = re.compile(
    r"\.\s*all\s*\(\s*\)[^\n]*\n[^\n]*\bfor\b[^\n]*\n[^\n]*\.\w+",
)

# FastAPI Depends() — dependency injection chain.
_FASTAPI_DEPENDS_RE = re.compile(r"\bDepends\s*\(\s*(\w+)\s*\)")

# Flask. Surface routes (``@app.route``, ``@bp.route``, ``@blueprint.route``)
# and known anti-patterns (``debug=True`` to ``app.run``, hard-coded
# ``SECRET_KEY``, raw query parameters into SQL, missing CSRF protection).
# Captures the route path so the finding can list it.
_FLASK_ROUTE_RE = re.compile(
    r"@\s*(?:\w+)\.route\s*\(\s*[\"']([^\"']+)[\"']",
)
# ``app.run(debug=True)`` — leaks the Werkzeug debugger to anyone who
# can reach the host. Real-world CVE class.
_FLASK_DEBUG_TRUE_RE = re.compile(
    r"\.run\s*\([^)]*\bdebug\s*=\s*True",
)
# ``app.config['SECRET_KEY'] = '...'`` or
# ``app.secret_key = '...'`` with a string literal. Flagged because
# a literal SECRET_KEY in source is the canonical session-forgery
# vector. Reads from env (``os.environ``, ``config(...)``) are safe.
_FLASK_SECRET_KEY_LITERAL_RE = re.compile(
    r"""(?:
        (?:app|application)\.config\s*\[\s*[\"']SECRET_KEY[\"']\s*\]\s*=\s*[\"'][^\"']+[\"']
        |
        (?:app|application)\.secret_key\s*=\s*[\"'][^\"']+[\"']
    )""",
    re.VERBOSE,
)

# Late-binding closure: ``lambda x: i*x`` inside a loop where ``i``
# changes per iteration. Pattern: ``for i in ...:\n  ... lambda``
# in same body. Caller verifies same-loop scope.
_LAMBDA_IN_LOOP_RE = re.compile(
    r"^\s+for\s+(\w+)\s+in\s[^\n]+:\s*\n[\s\S]{0,200}?lambda\b",
    re.MULTILINE,
)

# ``except SomeError: pass`` — silently swallowing exceptions. The
# capture group preserves the exception clause (between ``except`` and
# ``:``) so ``detect_except_pass`` can suppress narrow-typed catches
# (``except (OSError, UnicodeDecodeError): pass``) which are
# legitimate "the file went away" handlers, not anti-patterns.
_EXCEPT_PASS_RE = re.compile(r"^\s*except\b([^:]*):\s*\n\s*pass\s*\n", re.MULTILINE)

# Exception clauses that DO NOT count as anti-patterns when followed by
# pass. These are typically narrow OS / parse / unicode errors that
# legitimately mean "skip this file / record". Anything else (bare
# ``except:``, ``except Exception``, custom exception classes) still
# fires.
_LEGITIMATE_NARROW_EXCEPTIONS = frozenset(
    {
        "OSError",
        "IOError",  # alias of OSError on Py3
        "FileNotFoundError",
        "PermissionError",
        "NotADirectoryError",
        "IsADirectoryError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "UnicodeError",
        "JSONDecodeError",
        "TimeoutError",
        "ConnectionError",
        "BrokenPipeError",
        "EOFError",
        "subprocess.TimeoutExpired",
        "subprocess.CalledProcessError",
        # Optional-dependency / feature-gating patterns: legitimately
        # swallowed when probing whether an optional package is
        # installed.
        "ImportError",
        "ModuleNotFoundError",
        # Concurrency primitives where a missed-state pass is the
        # documented contract (e.g. queue.get_nowait()).
        "KeyError",
        "AttributeError",
    }
)


def _except_clause_is_narrow(clause: str) -> bool:
    """True when ``except`` clause names only narrow OS/parse exceptions
    that are legitimately swallowed — e.g. ``OSError``, tuples thereof,
    ``(OSError, UnicodeDecodeError)``. Returns False for bare ``except``,
    ``Exception``, ``BaseException``, or any name not in the allowlist.
    """
    cleaned = clause.strip()
    if not cleaned:
        # bare ``except:`` — never narrow
        return False
    # Strip ``as exc`` and surrounding whitespace
    cleaned = re.sub(r"\s+as\s+\w+\s*$", "", cleaned).strip()
    # Strip outer parens for tuple form
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    # Split on commas and check every name
    names = [n.strip() for n in cleaned.split(",") if n.strip()]
    if not names:
        return False
    return all(name in _LEGITIMATE_NARROW_EXCEPTIONS for name in names)


# ``except Exception:`` (broad) — catches too much. Less severe than
# bare ``except:`` but still flagged.
_BROAD_EXCEPT_RE = re.compile(r"^\s*except\s+(?:Exception|BaseException)\s*(?:as\s+\w+\s*)?:", re.MULTILINE)

# ``async for`` missing on async iterators (StreamReader, async generators)
# — heuristic: ``for X in <name>`` where the iterator type hint suggests
# async (AsyncIterator, AsyncGenerator, AsyncIterable).
_ASYNC_ITER_HINT = re.compile(r"AsyncIterator|AsyncGenerator|AsyncIterable")


def _project_root_for_conn(conn: sqlite3.Connection) -> str:
    """Resolve the project root for ``conn`` by inspecting its
    sqlite database file path — paths in the index are stored
    relative to ``<project_root>``, so we need the project root to
    open them regardless of the caller's cwd.

    Returns the empty string when the DB is in-memory or the path
    can't be derived; callers fall back to relative-path open().
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except Exception:
        return ""
    db_path = row[2] if row else ""
    if not db_path:
        return ""
    # ``.roam/index.db`` → project root is the dir containing ``.roam/``
    import os.path as _osp

    parent = _osp.dirname(db_path)
    if _osp.basename(parent) == ".roam":
        return _osp.dirname(parent)
    return parent


# Per-process cache of file-text reads. With 19 detectors each calling
# ``_file_text`` per file, an uncached implementation does 19×N disk
# reads. Cache keyed by ``(id(conn), file_id)`` so distinct DB
# connections don't collide. Cleared at module unload (or via
# ``_clear_file_text_cache()`` in tests). Bounded in size at 4096
# entries to avoid unbounded growth on huge repos; LRU-evicted.
from collections import OrderedDict as _OrderedDict

_FILE_TEXT_CACHE: _OrderedDict = _OrderedDict()
_FILE_TEXT_CACHE_MAX = 4096


def _clear_file_text_cache() -> None:
    """Clear the file-text cache. Call from tests that want clean state."""
    _FILE_TEXT_CACHE.clear()


def _file_text(conn: sqlite3.Connection, file_id: int) -> str | None:
    """Read the source text of a file via roam.index — but the index
    doesn't store source. Instead we read from disk via the file path
    resolved against the project root (paths in the index are
    project-relative, so a bare ``open(path)`` fails when the caller
    isn't sitting at the project root).

    Caches results across calls within a process so all 19+ detectors
    pay the disk read once per file rather than 19+ times.
    """
    cache_key = (id(conn), file_id)
    cached = _FILE_TEXT_CACHE.get(cache_key)
    if cached is not None:
        # LRU bump
        _FILE_TEXT_CACHE.move_to_end(cache_key)
        return cached if cached != "" else None  # sentinel for "tried, failed"
    row = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        _FILE_TEXT_CACHE[cache_key] = ""
        return None
    path = row[0]
    if not path:
        _FILE_TEXT_CACHE[cache_key] = ""
        return None
    # Resolve project-relative path via the DB's location.
    root = _project_root_for_conn(conn)
    if root:
        import os.path as _osp

        if not _osp.isabs(path):
            path = _osp.join(root, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        _FILE_TEXT_CACHE[cache_key] = ""
        return None
    _FILE_TEXT_CACHE[cache_key] = text
    # LRU eviction
    if len(_FILE_TEXT_CACHE) > _FILE_TEXT_CACHE_MAX:
        _FILE_TEXT_CACHE.popitem(last=False)
    return text


# Triple-quoted strings (greedy across newlines), single/double quoted
# strings (per-line), and ``#`` comments. Replaced with spaces of the
# same length so line numbers and column offsets stay stable for
# downstream regex matching.
_TRIPLE_QUOTE_RE = re.compile(
    r'(""".*?"""|\'\'\'.*?\'\'\')',
    re.DOTALL,
)
_SINGLE_QUOTE_RE = re.compile(r'("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')')
_COMMENT_RE = re.compile(r"#[^\n]*")


_STRIP_CACHE: _OrderedDict = _OrderedDict()
_STRIP_CACHE_MAX = 4096


def _strip_strings_and_comments(text: str) -> str:
    """Replace strings + comments with same-length whitespace so the
    detector regexes don't false-match inside docstrings or comments.

    Length-preserving so ``text.count("\n", 0, match.start())`` still
    yields the original line number. Cached because all detectors
    that strip apply the same transform — without caching we re-strip
    once per detector per file.
    """
    if not text:
        return text
    # Cache key: id() of the original string (CPython gives unique
    # ids while alive). Combined with len() to catch the rare case
    # of id-reuse after string GC.
    key = (id(text), len(text))
    cached = _STRIP_CACHE.get(key)
    if cached is not None:
        _STRIP_CACHE.move_to_end(key)
        return cached

    def _blank_multiline(match: re.Match) -> str:
        # Per-SEGMENT instead of per-character: O(lines) not O(chars).
        # The per-char generator join was ~25% of the whole `roam algo`
        # wall time (424K matches × char-wise join on a full sweep).
        return "\n".join(" " * len(seg) for seg in match.group(0).split("\n"))

    def _blank_inline(match: re.Match) -> str:
        # Single-line strings and comments can't contain newlines.
        return " " * (match.end() - match.start())

    out = _TRIPLE_QUOTE_RE.sub(_blank_multiline, text)
    out = _SINGLE_QUOTE_RE.sub(_blank_inline, out)
    out = _COMMENT_RE.sub(_blank_inline, out)
    _STRIP_CACHE[key] = out
    if len(_STRIP_CACHE) > _STRIP_CACHE_MAX:
        _STRIP_CACHE.popitem(last=False)
    return out


# Call-scoped file filter (mirrors `_INCLUDE_TESTS_OVERRIDE`). When set to a set
# of file_ids, the idiom detectors analyze ONLY those files — turning the whole-
# project sweep (17s on roam-code) into a changed-files sweep (sub-second), which
# is what makes `roam verify --deep` viable as a post-edit review. None = all
# files (the default whole-project behavior is unchanged).
_SCOPE_FILE_IDS: set[int] | None = None


def set_idiom_scope(file_ids) -> None:
    """Restrict subsequent idiom-detector runs to ``file_ids`` (None = all).

    Callers MUST reset to None in a ``finally`` (the scope is a module global, so
    a leaked scope would silently narrow an unrelated later run)."""
    global _SCOPE_FILE_IDS
    _SCOPE_FILE_IDS = set(file_ids) if file_ids is not None else None


def _python_files(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return ``(file_id, path)`` for every Python file in the index (or only the
    files in the active ``_SCOPE_FILE_IDS`` when a scope is set)."""
    rows = conn.execute("SELECT id, path FROM files WHERE language = 'python'").fetchall()
    out = [(int(r[0]), r[1]) for r in rows]
    if _SCOPE_FILE_IDS is not None:
        out = [(fid, p) for fid, p in out if fid in _SCOPE_FILE_IDS]
    return out


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
        if text:
            text = _strip_strings_and_comments(text)
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
        if text:
            text = _strip_strings_and_comments(text)
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
        if text:
            text = _strip_strings_and_comments(text)
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


def _match_in_doc_or_comment(text: str, start: int) -> bool:
    """True if the match at ``start`` is inside a ``#`` comment or a triple-
    quoted docstring. Used by the detectors that must scan RAW text (they need
    intact quotes — e.g. the ``f"`` indicator, or a ``"|"`` separator) and so
    can't pre-strip strings: this keeps them from self-flagging their own
    documentation / example prose."""
    line_begin = text.rfind("\n", 0, start) + 1
    if text[line_begin:start].lstrip().startswith("#"):
        return True
    head = text[:start]
    return head.count('"""') % 2 == 1 or head.count("'''") % 2 == 1


def _logger_fstring_finding(text: str, match, sym_index, path: str) -> dict | None:
    """Map one logger-fstring regex match to a finding, or None when it's in a
    docstring/comment (self-doc) or has no enclosing symbol."""
    if _match_in_doc_or_comment(text, match.start()):
        return None
    line_no = text.count("\n", 0, match.start()) + 1
    sym = _enclosing_symbol(line_no, sym_index)
    if sym is None:
        return None
    return _idiom_finding(
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


def detect_logger_fstring(conn: sqlite3.Connection) -> list[dict]:
    """Find ``logger.info(f"...")`` — eager-format anti-pattern.

    Doesn't pre-strip strings/comments because the f-string indicator
    (``f"`` / ``f'``) requires the opening quote to be intact — the
    stripper blanks it out. Instead `_match_in_doc_or_comment` (in the
    per-match helper) skips matches inside docstrings/comments so a detector
    documenting ``logger.info(f"...")`` in its own docstring won't self-flag.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _LOGGER_FSTRING_RE.finditer(text):
            f = _logger_fstring_finding(text, match, sym_index, path)
            if f is not None:
                findings.append(f)
    return findings


def _async_function_ranges(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, int, int, str]]:
    """``(symbol_id, line_start, line_end, name)`` for async functions
    in ``file_id`` only. Used by sync-in-async detector to scope its
    scan to coroutine bodies."""
    return [
        (int(r[0]), int(r[1] or 0), int(r[2] or 0), r[3])
        for r in conn.execute(
            "SELECT id, line_start, line_end, name FROM symbols WHERE file_id = ? AND is_async = 1 ORDER BY line_start",
            (file_id,),
        ).fetchall()
    ]


def detect_sync_in_async(conn: sqlite3.Connection) -> list[dict]:
    """Find blocking sync IO calls inside ``async def`` bodies.

    Canonical Python production bug: ``requests.get`` / ``time.sleep`` /
    ``subprocess.run`` inside an async coroutine blocks the event
    loop, starving every other task on the loop. The fixes are
    well-known (``httpx.AsyncClient``, ``asyncio.sleep``, etc.) — this
    detector flags the exact line + suggests the swap.

    Scope: scan source text within the line range of every
    ``is_async=1`` symbol. Skip files with no async symbols.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        async_ranges = _async_function_ranges(conn, file_id)
        if not async_ranges:
            continue
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        lines = text.splitlines()
        for sym_id, start, end, sym_name in async_ranges:
            # Slice the body lines (1-indexed).
            body = "\n".join(lines[max(start - 1, 0) : end])
            for pattern, suggestion in _SYNC_IN_ASYNC_PATTERNS:
                for match in pattern.finditer(body):
                    # Compute 1-indexed line within the file.
                    body_offset = body.count("\n", 0, match.start())
                    line_no = start + body_offset
                    findings.append(
                        _idiom_finding(
                            task_id="py-sync-in-async",
                            detected_way="blocking-call",
                            symbol_id=sym_id,
                            symbol_name=sym_name,
                            file_path=path,
                            line_no=line_no,
                            reason=(
                                f"blocking ``{match.group(0)}`` inside ``async def {sym_name}`` starves the event loop"
                            ),
                            confidence="high",
                            fix=suggestion,
                        )
                    )
    return findings


def detect_star_import(conn: sqlite3.Connection) -> list[dict]:
    """Find ``from X import *`` — namespace pollution.

    These can shadow names silently and make ``mypy --strict`` /
    ``ruff F401`` impossible to use. Surfaces ALL star imports
    (low confidence: many codebases legitimately re-export this way
    in ``__init__.py``).
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _STAR_IMPORT_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            # Star imports are typically at module level — sym may be None
            # (no enclosing fn). Synthesise a module-level handle.
            if sym is None:
                # Use the first symbol in the file as the anchor
                if not sym_index:
                    continue
                sym = (sym_index[0][0], "<module>")
            findings.append(
                _idiom_finding(
                    task_id="py-star-import",
                    detected_way="namespace-pollution",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``from X import *`` shadows names silently and breaks static analysis",
                    confidence="low",
                    fix="from X import explicit_name1, explicit_name2",
                )
            )
    return findings


def detect_dict_keys_iter(conn: sqlite3.Connection) -> list[dict]:
    """Find ``for k in d.keys():`` — redundant, slower than ``for k in d``.

    Iterating a dict yields keys by default; ``.keys()`` allocates
    a view and adds a function-call cost on every iteration.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _DICT_KEYS_ITER_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-dict-keys-iter",
                    detected_way="redundant-keys",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``for k in d.keys()`` is redundant — iterating ``d`` yields keys directly",
                    confidence="high",
                    fix="for k in d:",
                )
            )
    return findings


def detect_async_with_missing(conn: sqlite3.Connection) -> list[dict]:
    """Find ``aiofiles.open(...)`` / ``httpx.AsyncClient(...)`` not
    inside an ``async with`` — async resource leak.

    Both are common in modern async Python. The async resource must
    be explicitly entered with ``async with`` or the connection /
    file pool isn't released.
    """
    findings: list[dict] = []
    patterns = [
        (_ASYNC_OPEN_RE, "aiofiles.open(...)", "async with aiofiles.open(...) as f:"),
        (_HTTPX_CLIENT_RE, "httpx.AsyncClient(...)", "async with httpx.AsyncClient() as client:"),
    ]
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for line_no, line in enumerate(text.splitlines(), 1):
            if "async with " in line:
                continue
            for pattern, what, fix in patterns:
                if pattern.search(line):
                    sym = _enclosing_symbol(line_no, sym_index)
                    if sym is None:
                        continue
                    findings.append(
                        _idiom_finding(
                            task_id="py-async-with-missing",
                            detected_way="async-resource-leak",
                            symbol_id=sym[0],
                            symbol_name=sym[1],
                            file_path=path,
                            line_no=line_no,
                            reason=f"``{what}`` not entered with ``async with`` — connection pool may leak",
                            confidence="high",
                            fix=fix,
                        )
                    )
    return findings


def detect_type_eq(conn: sqlite3.Connection) -> list[dict]:
    """Find ``type(x) == X`` — should be ``isinstance(x, X)``.

    ``type() ==`` only matches the exact class; doesn't catch
    subclasses. ``isinstance`` is what users almost always want.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _TYPE_EQ_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-type-eq",
                    detected_way="type-not-isinstance",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``type(x) == X`` misses subclasses; use ``isinstance(x, X)``",
                    confidence="high",
                    fix="isinstance(x, X)",
                )
            )
    return findings


def detect_pandas_iterrows(conn: sqlite3.Connection) -> list[dict]:
    """Find pandas ``DataFrame.iterrows()`` row loops.

    This is a Python-specific performance idiom rather than a universal
    complexity-class detector: pandas documents that ``itertuples()`` is
    generally faster and preserves dtypes better when row iteration is
    unavoidable.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text or "iterrows" not in text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _PANDAS_ITERROWS_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-pandas-iterrows",
                    detected_way="series-row-loop",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="pandas DataFrame.iterrows() yields Series rows; prefer vectorized ops or itertuples()",
                    confidence="medium",
                    fix="for row in df.itertuples(index=False): ...",
                )
            )
    return findings


def detect_async_not_awaited(conn: sqlite3.Connection) -> list[dict]:
    """Find calls to async functions that aren't ``await``ed.

    Calling an async function without ``await`` returns a coroutine
    object that's never executed — the call silently does nothing.
    This is a top-3 cause of "why doesn't my async code run?" on
    StackOverflow.

    Approach: build the set of known async function NAMES from the
    index. For each Python file, scan source text (after stripping
    strings/comments) for ``<name>(`` calls. If the call isn't
    preceded by ``await ``, ``asyncio.gather(``, ``asyncio.create_task(``,
    ``asyncio.run(``, ``loop.run_until_complete(``, ``ensure_future(``,
    or ``= `` (assignment to coroutine for later use), flag it.

    Conservative: requires the called name to be known-async via the
    index, so we don't false-flag every `foo()` call when `foo` happens
    to share a name with an async function elsewhere (the index has
    the actual symbol — we're asking ``is THIS name async-only?``).
    """
    findings: list[dict] = []
    # Build set of known async function names. Filter to names that are
    # ONLY defined as async (no sync overload) to keep precision high.
    rows = conn.execute(
        "SELECT name, SUM(is_async) AS n_async, COUNT(*) AS n_total "
        "FROM symbols WHERE kind IN ('function', 'method') GROUP BY name"
    ).fetchall()
    async_names = {r[0] for r in rows if r[0] and r[1] and r[1] == r[2]}
    if not async_names:
        return findings

    # Pre-build a single regex of all async names. For most repos this
    # is <500 names; well under the regex engine limit.
    if len(async_names) > 1000:
        # Skip on huge repos — we'd scan every call site of every name.
        return findings
    # ``(?<![\w.])`` skips dotted attribute access (``rng.sample``,
    # ``Class.sample``) — those resolve to the attribute owner, not
    # a roam-indexed bare async function. Only bare ``name(`` calls
    # are considered. This avoids false positives where a stdlib
    # method shares a name with a project-internal async function
    # (caught on ``random.Random.sample`` vs ``_MockContext.sample``).
    name_pattern = re.compile(
        r"(?<![\w.])(" + "|".join(re.escape(n) for n in sorted(async_names, key=len, reverse=True)) + r")\s*\(",
    )
    safe_prefixes = (
        "await ",
        "asyncio.gather(",
        "asyncio.create_task(",
        "asyncio.run(",
        "asyncio.ensure_future(",
        "asyncio.wait(",
        "asyncio.wait_for(",
        "ensure_future(",
        "create_task(",
        "loop.run_until_complete(",
        "self.loop.run_until_complete(",
        # Function definition prefix — ``async def NAME(`` is a
        # declaration, not a call. Without this guard we false-flag
        # the function being defined as if it were calling itself.
        "def ",
    )

    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in name_pattern.finditer(text):
            # Look at the 80 chars before the match to check for safe prefixes
            # or assignment patterns.
            start = max(0, match.start() - 80)
            preceding = text[start : match.start()]
            # Skip if it's awaited/gathered/etc.
            if any(sp in preceding[-30:] for sp in safe_prefixes):
                continue
            # Skip if it's a coroutine assignment (=) within the last 5 chars
            # ``coro = some_async_fn(...)`` — legitimate "save for later".
            tail = preceding[-5:].strip()
            if tail.endswith("="):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-async-not-awaited",
                    detected_way="missing-await",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=(
                        f"call to async function ``{match.group(1)}`` not awaited — returns a coroutine that never runs"
                    ),
                    confidence="medium",
                    fix=f"await {match.group(1)}(...)",
                )
            )
    return findings


def detect_lambda_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """Find ``lambda`` in a ``for``-loop body — classic late-binding bug.

    The lambda captures the loop variable by reference, so all
    lambdas end up bound to the LAST value of the variable. Fix:
    add ``i=i`` default argument or use functools.partial.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _LAMBDA_IN_LOOP_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            var = match.group(1)
            # Precision: the late-binding bug requires the lambda to CAPTURE the
            # loop variable. A lambda whose body never references `var` — e.g. a
            # sort key `sort(key=lambda c: -len(c))` that happens to sit within
            # 200 chars of an unrelated `for nb in …:` — is safe. Check the
            # lambda's tail on its own line (strings/comments already blanked).
            lam_end = match.end()
            line_end = text.find("\n", lam_end)
            tail = text[lam_end : (line_end if line_end != -1 else len(text))]
            if not re.search(rf"\b{re.escape(var)}\b", tail):
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-lambda-in-loop",
                    detected_way="late-binding-closure",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=(
                        f"``lambda`` inside ``for {var} in …`` loop — late binding: "
                        f"all lambdas bind to the LAST value of {var}"
                    ),
                    confidence="medium",
                    fix=f"lambda x, {var}={var}: ...  # capture by default arg",
                )
            )
    return findings


def detect_except_pass(conn: sqlite3.Connection) -> list[dict]:
    """Find ``except X: pass`` — silently swallowing exceptions. Skips
    narrow-typed catches like ``except OSError: pass`` and tuples of
    OS/parse errors which are legitimately swallowed when iterating over
    files."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _EXCEPT_PASS_RE.finditer(text):
            clause = match.group(1)
            if _except_clause_is_narrow(clause):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-except-pass",
                    detected_way="silent-swallow",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``except X: pass`` silently swallows the exception — log it or re-raise",
                    confidence="high",
                    fix="except X as exc:\n    logger.warning('...', exc_info=exc)\n    raise",
                )
            )
    return findings


def detect_broad_except(conn: sqlite3.Connection) -> list[dict]:
    """Find ``except Exception:`` / ``except BaseException:``."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _BROAD_EXCEPT_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-broad-except",
                    detected_way="catch-too-much",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``except Exception:`` catches more than intended — narrow to specific class(es)",
                    confidence="low",
                    fix="except (ValueError, KeyError):  # or whatever the actual cases are",
                )
            )
    return findings


def detect_django_n1(conn: sqlite3.Connection) -> list[dict]:
    """Find Django ORM N+1 patterns:

    * ``.objects.filter(...)`` / ``.get(...)`` *inside* a loop body.
    * ``.all()`` immediately followed by ``for x in qs:`` (suggests
      ``.select_related()`` / ``.prefetch_related()``).
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        # Require a Django ORM hint anywhere in the file. Without
        # ``.objects.`` (the manager indicator) or an explicit Django
        # import, a ``.all()`` shape might be a custom collection or a
        # different ORM — firing would be a false positive.
        has_django_hint = ".objects." in text or "from django" in text or "import django" in text
        if not has_django_hint:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _DJANGO_ALL_THEN_FOR.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            # Suppress when the queryset chain already eager-loads via
            # select_related or prefetch_related. Look back from the
            # ``.all()`` call to the assignment (or 200 chars max) for
            # those calls. They defuse the N+1 — firing here would be a
            # false positive.
            chain_start = max(0, match.start() - 200)
            chain = text[chain_start : match.start()]
            if "select_related(" in chain or "prefetch_related(" in chain:
                continue
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-django-n1",
                    detected_way="all-then-for",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``.all()`` then iterate — likely N+1; use ``.select_related()`` / ``.prefetch_related()``",
                    confidence="medium",
                    fix=".all().select_related('fk_field')",
                )
            )
        # filter/get inside loop: detect by walking lines, tracking
        # indent depth from preceding ``for``/``while``.
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if not _DJANGO_FILTER_IN_LOOP.search("\n" + line):
                continue
            # Look back up to 20 lines for ``for X in`` at lesser indent
            current_indent = len(line) - len(line.lstrip())
            in_loop = False
            for back in range(max(0, i - 20), i - 1):
                prev = lines[back]
                prev_indent = len(prev) - len(prev.lstrip())
                if prev_indent < current_indent and (
                    prev.lstrip().startswith("for ") or prev.lstrip().startswith("while ")
                ):
                    in_loop = True
                    break
            if not in_loop:
                continue
            sym = _enclosing_symbol(i, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-django-n1",
                    detected_way="query-in-loop",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=i,
                    reason="Django ORM query inside loop — N+1; lift the query out or use ``.in_bulk()``",
                    confidence="high",
                    fix="ids = [x.id for x in items]; lookup = Model.objects.in_bulk(ids)",
                )
            )
    return findings


def detect_sqlalchemy_lazy(conn: sqlite3.Connection) -> list[dict]:
    """Find SQLAlchemy ``.all()`` then iterate-with-attribute patterns.

    Heuristic: the file has ``relationship(`` (so it defines models)
    AND the ``.all()`` call is immediately followed by a ``for``-loop
    that accesses an attribute on each item. The accessed attribute
    is likely a relationship that triggers a lazy SELECT per row.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        # Quick reject: must have at least one relationship() declaration
        if not _SQLALCHEMY_RELATIONSHIP.search(text):
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _SQLALCHEMY_ALL_THEN_DOT.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            # Suppress when the query expression already eager-loads via
            # joinedload / selectinload / contains_eager. Look back from
            # the ``.all()`` call to the start of the statement (or 200
            # chars, whichever is shorter).
            chain_start = max(0, match.start() - 200)
            chain = text[chain_start : match.start()]
            if (
                "joinedload(" in chain
                or "selectinload(" in chain
                or "contains_eager(" in chain
                or "subqueryload(" in chain
            ):
                continue
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-sqlalchemy-lazy",
                    detected_way="lazy-load-in-loop",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=".all() then for-loop accessing attr — likely lazy-load N+1",
                    confidence="medium",
                    fix=".options(selectinload(Model.relationship)).all()",
                )
            )
    return findings


def detect_fastapi_depends(conn: sqlite3.Connection) -> list[dict]:
    """Inventory FastAPI ``Depends(X)`` provider chain.

    Not strictly an anti-pattern; surfaces as info-level findings so
    agents can discover the dependency graph for FastAPI apps.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        if "Depends(" not in text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        seen: set[tuple[int, str]] = set()
        for match in _FASTAPI_DEPENDS_RE.finditer(text):
            provider = match.group(1)
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            key = (sym[0], provider)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _idiom_finding(
                    task_id="py-fastapi-depends",
                    detected_way="dependency-provider",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=f"FastAPI dependency: depends on ``{provider}``",
                    confidence="high",
                    fix="",
                )
            )
    return findings


def detect_flask_routes(conn: sqlite3.Connection) -> list[dict]:
    """Inventory Flask ``@app.route`` / ``@blueprint.route`` decorators.

    Info-level: surfaces routes so agents can discover the URL surface
    (analogous to ``py-fastapi-depends`` for FastAPI). Each finding
    names the route's URL path in the reason field.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        # Quick reject: skip files without any Flask context. We require
        # ``flask`` import OR a ``.route(`` decorator pattern OR an
        # ``@app.route`` style. The decorator regex itself is a strong
        # enough signal but the prefilter saves a regex sweep on
        # non-Flask files.
        if "flask" not in text.lower() and ".route(" not in text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _FLASK_ROUTE_RE.finditer(text):
            url = match.group(1)
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-flask-routes",
                    detected_way="route-decorator",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=f"Flask route registered at ``{url}``",
                    confidence="high",
                    fix="",
                )
            )
    return findings


def detect_flask_debug_true(conn: sqlite3.Connection) -> list[dict]:
    """Find ``app.run(debug=True)`` — leaks the Werkzeug debugger.

    Real-world CVE class. The Werkzeug debugger exposes an interactive
    Python REPL bound to the HTTP port; anyone who can reach the host
    can run arbitrary code in the application's process.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        if ".run(" not in text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _FLASK_DEBUG_TRUE_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-flask-debug-true",
                    detected_way="debug-true",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``debug=True`` exposes the Werkzeug debugger; gate behind an env check",
                    confidence="high",
                    fix="app.run(debug=os.environ.get('FLASK_DEBUG') == '1')",
                )
            )
    return findings


def detect_flask_secret_key_literal(conn: sqlite3.Connection) -> list[dict]:
    """Find a literal Flask ``SECRET_KEY`` in source.

    A hard-coded SECRET_KEY in the repository compromises every signed
    cookie / session token the app has issued — anyone with read access
    to the source can forge sessions. Reads from environment variables
    or config files are skipped (the regex requires a string literal
    immediately after ``=``).
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        if "SECRET_KEY" not in text and "secret_key" not in text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _FLASK_SECRET_KEY_LITERAL_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-flask-secret-key-literal",
                    detected_way="hardcoded-secret",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason="``SECRET_KEY`` is a string literal in source — read from env / secret manager instead",
                    confidence="high",
                    fix="app.config['SECRET_KEY'] = os.environ['SECRET_KEY']",
                )
            )
    return findings


def detect_sync_calls_async_via_graph(conn: sqlite3.Connection) -> list[dict]:
    """Use the call graph to find sync functions calling async ones.

    Complement to ``detect_async_not_awaited`` (regex-based on names).
    This walks ``edges.kind='call'`` and reports edges where source
    is sync and target is async. Strong-evidence finding because it's
    backed by the indexed graph not text matching.

    Skips when the source itself is a known wrapper (test runner,
    ``asyncio.run``-style entry point) by name.
    """
    findings: list[dict] = []
    # Names that are legitimate sync→async entry points and should be
    # excluded from the scan.
    skip_source_names = {"main", "cli", "run", "entrypoint", "_main", "__main__"}
    # W512: edge-kind vocabulary lives in roam.db.edge_kinds — pure
    # call edges only for sync→async detection (we want the call
    # graph, not reference reads).
    _idioms_call_kind_ph = ", ".join("?" for _ in CALL_EDGE_KINDS)
    try:
        rows = conn.execute(
            f"""
            SELECT e.source_id, src.name AS src_name, src.is_async AS src_async,
                   e.target_id, tgt.name AS tgt_name,
                   src_f.path AS src_file, src.line_start AS src_line
            FROM edges e
            JOIN symbols src ON src.id = e.source_id
            JOIN symbols tgt ON tgt.id = e.target_id
            JOIN files src_f ON src_f.id = src.file_id
            WHERE e.kind IN ({_idioms_call_kind_ph})
              AND src.kind IN ('function', 'method')
              AND tgt.kind IN ('function', 'method')
              AND tgt.is_async = 1
              AND src.is_async = 0
              AND COALESCE(src_f.file_role, '') != 'test'
            """,
            CALL_EDGE_KINDS,
        ).fetchall()
    except Exception:
        return findings
    seen: set[tuple[int, int]] = set()
    for r in rows:
        sid = int(r["source_id"])
        tid = int(r["target_id"])
        key = (sid, tid)
        if key in seen:
            continue
        seen.add(key)
        if r["src_name"] in skip_source_names:
            continue
        findings.append(
            _idiom_finding(
                task_id="py-sync-calls-async",
                detected_way="missing-await-graph",
                symbol_id=sid,
                symbol_name=r["src_name"],
                file_path=r["src_file"],
                line_no=int(r["src_line"] or 0),
                reason=(
                    f"sync ``{r['src_name']}`` calls async ``{r['tgt_name']}`` — "
                    f"either ``await`` it (mark caller async) or use ``asyncio.run``"
                ),
                confidence="medium",
                fix=f"async def {r['src_name']}(...): await {r['tgt_name']}(...)",
            )
        )
    return findings


def detect_lock_without_with(conn: sqlite3.Connection) -> list[dict]:
    """Find ``lock.acquire()`` without ``with``-block — lock leak.

    Threading.Lock and threading.RLock are context managers; using
    ``acquire()`` directly without try/finally is a deadlock waiting
    to happen on exception paths.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in _LOCK_ACQUIRE_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            # Skip if this line is inside a try/with block — heuristic:
            # check the 200 chars before for ``with`` or ``try:``.
            preceding = text[max(0, match.start() - 200) : match.start()]
            if "with " in preceding[-80:] or "try:" in preceding[-200:]:
                continue
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            var = match.group(1)
            findings.append(
                _idiom_finding(
                    task_id="py-lock-without-with",
                    detected_way="lock-leak",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=f"``{var}.acquire()`` outside try/with — lock may leak on exception",
                    confidence="medium",
                    fix=f"with {var}: ...",
                )
            )
    return findings


def detect_open_without_with(conn: sqlite3.Connection) -> list[dict]:
    """Find ``open(...)`` calls that aren't inside a ``with`` block —
    real file resource leak pattern.

    Approach: scan each line; if it contains ``open("..."`` but no
    ``with`` keyword, flag it. Conservative — skips ``os.open`` /
    ``codecs.open`` (they're often legitimately not in with). Won't
    catch ``f = open(...)`` on a multi-line statement; for that we'd
    need AST parsing. Good enough for the common case.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for ln_no, line in enumerate(text.splitlines(), 1):
            if "open(" not in line:
                continue
            # Strip strings/comments roughly to avoid matching ``"open("`` text.
            # Heuristic: drop everything after a # not inside a string.
            stripped = line.split("#", 1)[0]
            if "open(" not in stripped:
                continue
            if "with " in stripped or "async with " in stripped:
                continue
            if not _OPEN_WITHOUT_WITH_RE.search(stripped):
                continue
            sym = _enclosing_symbol(ln_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-open-without-with",
                    detected_way="resource-leak",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=ln_no,
                    reason="``open(...)`` outside a ``with`` block — file may not be closed on exception",
                    confidence="medium",
                    fix="with open(...) as f:\n    ...",
                )
            )
    return findings


def has_decorator(decorators: str | None, name: str) -> bool:
    """Return True when ``decorators`` contains a top-level decorator
    matching ``name`` exactly (or ``@name(...)``).

    The substring naive check matches false positives — e.g.
    ``LIKE '%pytest.fixture%'`` matches a click.option help text
    that *mentions* pytest.fixture. This helper splits on the
    comma-joined decorator list and checks each part starts with
    ``@<name>`` followed by ``(``, space, or end of string.
    """
    if not decorators:
        return False
    needle = name.lstrip("@").lower()
    for part in decorators.split(","):
        part = part.strip()
        if not part.startswith("@"):
            continue
        body = part[1:].lower().strip()
        # Match exact, or ``name(`` prefix, or ``name `` prefix.
        if body == needle or body.startswith(needle + "(") or body.startswith(needle + " "):
            return True
    return False


def fixture_kind(decorators: str | None) -> str | None:
    """Return ``"pytest"`` / ``"asyncio"`` / ``"parametrize"`` / ``None``
    based on the test-suite decorators present.

    Used by ``roam context`` to surface ``[pytest fixture]`` etc. badges
    so agents reading test code immediately see fixture lifecycle.
    """
    if not decorators:
        return None
    if has_decorator(decorators, "pytest.fixture") or has_decorator(decorators, "fixture"):
        # @pytest.fixture or just @fixture (when imported)
        return "pytest fixture"
    if has_decorator(decorators, "pytest.mark.parametrize"):
        return "parametrize"
    if has_decorator(decorators, "pytest.mark.asyncio"):
        return "async test"
    if has_decorator(decorators, "pytest.mark.skip") or has_decorator(decorators, "pytest.mark.skipif"):
        return "skipped test"
    return None


def is_model_class(signature: str | None, decorators: str | None) -> tuple[bool, str | None]:
    """Heuristic: return ``(is_model, kind_label)`` for a class.

    Recognises:
    * ``BaseModel`` subclass → Pydantic model
    * ``@dataclass`` / ``@dataclasses.dataclass`` → stdlib dataclass
    * ``@pydantic.dataclasses.dataclass`` → pydantic dataclass
    * ``@attr.s`` / ``@attrs.define`` → attrs
    * ``msgspec.Struct`` subclass → msgspec
    * ``NamedTuple`` subclass / ``@typing.NamedTuple`` → typed tuple
    * ``TypedDict`` subclass → TypedDict
    * ``Enum`` / ``IntEnum`` / ``StrEnum`` → enum

    Returned label is one of: ``pydantic``, ``dataclass``, ``attrs``,
    ``msgspec``, ``namedtuple``, ``typeddict``, ``enum``, or ``None``.
    Useful for ``roam context`` to display a model badge.
    """
    sig = (signature or "").lower()
    dec = (decorators or "").lower()
    # Decorator-based first (cheaper, more deterministic)
    if "@dataclass" in dec or "@dataclasses.dataclass" in dec:
        return True, "dataclass"
    if "@pydantic.dataclasses" in dec:
        return True, "pydantic-dataclass"
    if "@attr.s" in dec or "@attrs.define" in dec or "@attr.define" in dec:
        return True, "attrs"
    # Inheritance-based
    if "basemodel" in sig and "pydantic" not in sig:
        # Common: ``class User(BaseModel):`` (BaseModel imported from pydantic)
        return True, "pydantic"
    if "pydantic.basemodel" in sig:
        return True, "pydantic"
    if "msgspec.struct" in sig or "(struct)" in sig:
        return True, "msgspec"
    if "namedtuple" in sig:
        return True, "namedtuple"
    if "typeddict" in sig:
        return True, "typeddict"
    if "(enum)" in sig or "(intenum)" in sig or "(strenum)" in sig or "(flag)" in sig:
        return True, "enum"
    return False, None


# A regex built from a ``"|".join(...)`` alternation. Matched on RAW source (the
# ``|`` inside the string literal IS the signal, so this one detector does NOT
# strip string contents). Captures both quote styles.
_PIPE_JOIN_RE = re.compile(r"""['"]\|['"]\s*\.\s*join\(""")


def detect_regex_alternation_join(conn: sqlite3.Connection) -> list[dict]:
    """Flag ``re.compile("|".join(<variable collection>))`` — the speed flaw.

    Python's ``re`` engine re-tries every alternative at each text position, so a
    regex whose body is an N-way alternation of literals is O(text × N) to match.
    Built from a RUNTIME collection (a variable or comprehension, not a small
    literal list) N can be large — measured 9.6s on roam's own dead-code test
    scan (641 names × ~20 MB), fixed by switching to O(text) word-set membership.

    Precision: requires (a) ``re.compile`` within ~220 chars before the join and
    (b) the join argument to be a NON-literal collection (first token after
    ``join(`` is not ``[ ( { ' "`` — i.e. a name/call/comprehension, not a small
    hand-written list). That admits the real flaw shapes (``join(names)``,
    ``join(re.escape(n) for n in seq)``, ``join(sorted(xs))``) while skipping
    ``join(["GET","POST"])``-style small constant alternations.
    """
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if not text or ('"|"' not in text and "'|'" not in text):
            continue
        sym_index = None
        for m in _PIPE_JOIN_RE.finditer(text):
            start = m.start()
            if "re.compile" not in text[max(0, start - 220) : start]:
                continue
            # A tool that documents this very pattern must not flag its own prose.
            if _match_in_doc_or_comment(text, start):
                continue
            # First non-space char of the join argument.
            j = m.end()
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j >= len(text) or text[j] in "[({\"'":
                continue  # literal collection / generator-in-parens → not the flaw
            if sym_index is None:
                sym_index = _line_to_symbol(conn, file_id)
            line_no = text.count("\n", 0, start) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            findings.append(
                _idiom_finding(
                    task_id="py-regex-alt-join",
                    detected_way="alternation-regex",
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=(
                        'regex compiled from a `"|".join(<collection>)` alternation; '
                        "`re` re-tries every alternative at each position, so matching is "
                        "O(text x N). On a large collection this dominates (9.6s in roam's "
                        "own dead-code scan before the fix)."
                    ),
                    confidence="medium",
                    fix=(
                        "For LITERAL alternations, tokenize once and test set membership: "
                        "`names = frozenset(parts); hits = names.intersection("
                        "re.findall(r'\\w+', text))` — O(text), independent of N. For "
                        "overlapping / non-word patterns use an Aho-Corasick automaton."
                    ),
                )
            )
    return findings


# ---- loop-body performance idioms (2026-06-11 wave) -----------------------
#
# Six classic quadratic-or-worse shapes that survive review because each
# iteration looks innocent. All share the lambda-in-loop detection model:
# a loop header followed by the trigger pattern within a bounded body
# window (strings/comments already blanked by the caller pipeline).

_LOOP_PREFIX = r"^(?P<ind>[ \t]*)(?:for|while)\s[^\n]*:\s*\n[\s\S]{0,300}?"

# counts[k] = counts.get(k, 0) + 1  → collections.Counter / defaultdict(int)
_MANUAL_COUNTER_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX + r"(?<![\w.])(?P<name>\w+)\[(?P<key>[^\]]+)\]\s*=\s*(?P=name)\.get\((?P=key),\s*0\)\s*\+\s*1",
    re.MULTILINE,
)
# acc = acc + [x]  → acc.append(x) / acc.extend(...)  (quadratic rebuild)
_LIST_REASSIGN_CONCAT_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX + r"(?<![\w.])(?P<name>\w+)\s*=\s*(?P=name)\s*\+\s*\[",
    re.MULTILINE,
)
# acc.append(x) ... acc.sort() / sorted(acc) in the SAME loop body
# (sorting a fresh per-iteration collection is fine; sorting the
# accumulator every pass is O(n^2 log n) → bisect.insort / one sort after)
_APPEND_THEN_SORT_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX
    + r"(?<![\w.])(?P<name>\w+)\.append\([^\n]*\)[\s\S]{0,200}?(?:(?<![\w.])(?P=name)\.sort\(|sorted\(\s*(?P=name)\b)",
    re.MULTILINE,
)
# queue.pop(0) in a loop — O(n) per dequeue → collections.deque.popleft()
_POP0_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX + r"(?P<name>\w+)\.pop\(\s*0\s*\)",
    re.MULTILINE,
)
# copy.deepcopy(...) per iteration — often hoistable or shallow-copyable
_DEEPCOPY_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX + r"\bdeepcopy\(",
    re.MULTILINE,
)
# pd.concat / np.concatenate / np.vstack per iteration — quadratic copying;
# collect parts in a list and concat ONCE after the loop
_FRAME_CONCAT_IN_LOOP_RE = re.compile(
    _LOOP_PREFIX + r"\b(?:pd\.concat|np\.concatenate|np\.vstack|np\.hstack)\(",
    re.MULTILINE,
)


def _detect_loop_idiom(
    conn: sqlite3.Connection,
    regex: "re.Pattern[str]",
    *,
    task_id: str,
    detected_way: str,
    reason: str,
    fix: str,
    confidence: str,
) -> list[dict]:
    """Shared scan for the loop-body performance idioms above. The reason/fix
    strings may reference ``{name}`` — replaced with the first capture group
    (the accumulator/collection variable) when the regex has one."""
    findings: list[dict] = []
    for file_id, path in _python_files(conn):
        text = _file_text(conn, file_id)
        if text:
            text = _strip_strings_and_comments(text)
        if not text:
            continue
        sym_index = _line_to_symbol(conn, file_id)
        for match in regex.finditer(text):
            # Indent guard: the trigger must sit INSIDE the loop body. The
            # window regex alone also matches code AFTER the loop (dogfood
            # 2026-06-11: 'append in loop, sort once after' — the correct
            # idiom — accounted for most sort-in-loop hits). Compare the
            # trigger line's indentation with the loop header's.
            header_indent = len(match.group("ind") or "")
            line_start = text.rfind("\n", 0, match.end() - 1) + 1
            trigger_line = text[line_start : match.end()]
            trigger_indent = len(trigger_line) - len(trigger_line.lstrip(" \t"))
            if trigger_indent <= header_indent:
                continue
            line_no = text.count("\n", 0, match.end()) + 1
            sym = _enclosing_symbol(line_no, sym_index)
            if sym is None:
                continue
            name = match.group("name") if "name" in regex.groupindex else ""
            findings.append(
                _idiom_finding(
                    task_id=task_id,
                    detected_way=detected_way,
                    symbol_id=sym[0],
                    symbol_name=sym[1],
                    file_path=path,
                    line_no=line_no,
                    reason=reason.format(name=name),
                    confidence=confidence,
                    fix=fix.format(name=name),
                )
            )
    return findings


def detect_manual_counter_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``counts[k] = counts.get(k, 0) + 1`` inside a loop — hand-rolled
    Counter. collections.Counter(iterable) is C-speed and clearer."""
    return _detect_loop_idiom(
        conn,
        _MANUAL_COUNTER_IN_LOOP_RE,
        task_id="py-manual-counter",
        detected_way="dict-get-increment",
        reason="hand-rolled counting via ``{name}[k] = {name}.get(k, 0) + 1`` in a loop",
        fix="from collections import Counter; {name} = Counter(iterable)  # or defaultdict(int)",
        confidence="high",
    )


def detect_list_reassign_concat_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``acc = acc + [x]`` inside a loop — rebuilds the list every pass
    (quadratic). ``append``/``extend`` mutate in place at O(1) amortized."""
    return _detect_loop_idiom(
        conn,
        _LIST_REASSIGN_CONCAT_IN_LOOP_RE,
        task_id="py-quadratic-list-concat",
        detected_way="list-reassign-concat",
        reason="``{name} = {name} + [...]`` in a loop copies the whole list every iteration (O(n^2))",
        fix="{name}.append(item)  # or {name}.extend(items)",
        confidence="high",
    )


def detect_append_then_sort_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``acc.append(...)`` then ``acc.sort()``/``sorted(acc)`` in the same
    loop body — re-sorts the accumulator every pass (O(n^2 log n)).

    Confidence is MEDIUM, not high: the regex cannot tell a persistent
    accumulator from a collection rebuilt fresh each outer iteration
    (e.g. ``for g in graphs: g.inputs.append(...); g.inputs.sort()`` sorts
    a per-graph list once — legitimate). Dogfooded 2026-06-11: ~half the
    hits on this repo were the fresh-per-iteration shape."""
    return _detect_loop_idiom(
        conn,
        _APPEND_THEN_SORT_IN_LOOP_RE,
        task_id="py-sort-in-loop",
        detected_way="append-then-sort",
        reason="``{name}`` is appended to AND re-sorted inside the same loop (O(n^2 log n) if it persists across iterations)",
        fix="bisect.insort({name}, item)  # keeps it sorted in O(n) per insert; or sort ONCE after the loop",
        confidence="medium",
    )


def detect_pop0_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``queue.pop(0)`` inside a loop — every dequeue shifts the whole list
    (O(n)). collections.deque.popleft() is O(1)."""
    return _detect_loop_idiom(
        conn,
        _POP0_IN_LOOP_RE,
        task_id="py-pop0-queue",
        detected_way="list-as-queue",
        reason="``{name}.pop(0)`` in a loop shifts every remaining element (O(n) per dequeue)",
        fix="from collections import deque; {name} = deque(items); {name}.popleft()",
        confidence="high",
    )


def detect_deepcopy_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``copy.deepcopy(...)`` per iteration — frequently hoistable (template
    copied from an invariant) or replaceable with a shallow copy."""
    return _detect_loop_idiom(
        conn,
        _DEEPCOPY_IN_LOOP_RE,
        task_id="py-deepcopy-in-loop",
        detected_way="deepcopy-per-iteration",
        reason="``deepcopy`` inside a loop — full recursive copy every iteration",
        fix="hoist the deepcopy above the loop if the source is invariant; use dict(x)/list(x)/x.copy() when one level suffices",
        confidence="medium",
    )


def detect_frame_concat_in_loop(conn: sqlite3.Connection) -> list[dict]:
    """``pd.concat``/``np.concatenate``/``np.vstack`` per iteration — each
    call copies the whole accumulated frame/array (quadratic). Collect the
    parts and concatenate once."""
    return _detect_loop_idiom(
        conn,
        _FRAME_CONCAT_IN_LOOP_RE,
        task_id="py-frame-concat-in-loop",
        detected_way="concat-per-iteration",
        reason="DataFrame/array concatenation inside a loop copies all accumulated rows every iteration",
        fix="parts.append(chunk) inside the loop; ONE pd.concat(parts) / np.concatenate(parts) after it",
        confidence="high",
    )


# Detector registry — same shape ``cmd_math`` expects from ``_MATH_DETECTORS``
# (task_id, pattern_id, detect_fn). Re-exported so registration is one
# import line elsewhere.
PYTHON_IDIOM_DETECTORS = [
    ("py-regex-alt-join", "alternation-regex", detect_regex_alternation_join),
    ("py-mutable-default-arg", "default-mutable", detect_mutable_default_arg),
    ("py-bare-except", "catch-all", detect_bare_except),
    ("py-none-eq", "eq-not-is", detect_none_eq),
    ("py-logger-fstring", "eager-format", detect_logger_fstring),
    ("py-sync-in-async", "blocking-call", detect_sync_in_async),
    ("py-open-without-with", "resource-leak", detect_open_without_with),
    ("py-star-import", "namespace-pollution", detect_star_import),
    ("py-dict-keys-iter", "redundant-keys", detect_dict_keys_iter),
    ("py-async-not-awaited", "missing-await", detect_async_not_awaited),
    ("py-async-with-missing", "async-resource-leak", detect_async_with_missing),
    ("py-type-eq", "type-not-isinstance", detect_type_eq),
    ("py-pandas-iterrows", "series-row-loop", detect_pandas_iterrows),
    ("py-lock-without-with", "lock-leak", detect_lock_without_with),
    ("py-sync-calls-async", "missing-await-graph", detect_sync_calls_async_via_graph),
    ("py-django-n1", "django-orm", detect_django_n1),
    ("py-sqlalchemy-lazy", "sqla-lazy", detect_sqlalchemy_lazy),
    ("py-fastapi-depends", "fastapi-di", detect_fastapi_depends),
    ("py-flask-routes", "flask-route", detect_flask_routes),
    ("py-flask-debug-true", "flask-debug-leak", detect_flask_debug_true),
    ("py-flask-secret-key-literal", "flask-hardcoded-secret", detect_flask_secret_key_literal),
    ("py-lambda-in-loop", "late-binding-closure", detect_lambda_in_loop),
    ("py-except-pass", "silent-swallow", detect_except_pass),
    ("py-broad-except", "catch-too-much", detect_broad_except),
    # loop-body performance idioms (2026-06-11 wave)
    ("py-manual-counter", "dict-get-increment", detect_manual_counter_in_loop),
    ("py-quadratic-list-concat", "list-reassign-concat", detect_list_reassign_concat_in_loop),
    ("py-sort-in-loop", "append-then-sort", detect_append_then_sort_in_loop),
    ("py-pop0-queue", "list-as-queue", detect_pop0_in_loop),
    ("py-deepcopy-in-loop", "deepcopy-per-iteration", detect_deepcopy_in_loop),
    ("py-frame-concat-in-loop", "concat-per-iteration", detect_frame_concat_in_loop),
]


# Cheap applicability gate (2026-06-05): a detector whose trigger token can't
# appear in the changed text CANNOT produce a finding, so don't even run it.
# Library/framework detectors are the big win — irrelevant to most edits. A
# detector with NO entry here is always-applicable (its pattern is generic).
# This makes the post-edit `--deep` sweep CONTENT-DRIVEN: it fires only the
# checks the change could actually trip.
_IDIOM_TRIGGERS: dict[str, tuple[str, ...]] = {
    "py-regex-alt-join": ("re.compile", "_re.compile"),
    "py-logger-fstring": ("log",),  # logger. / logging. / log.<level>(
    "py-lambda-in-loop": ("lambda",),
    "py-open-without-with": ("open(",),
    "py-star-import": ("import *",),
    "py-dict-keys-iter": (".keys(",),
    "py-type-eq": ("type(",),
    "py-lock-without-with": (".acquire(",),
    "py-pandas-iterrows": ("iterrows",),
    "py-async-not-awaited": ("async", "await"),
    "py-async-with-missing": ("aiofiles", "httpx", "async with"),
    "py-sync-in-async": ("async def",),
    "py-sync-calls-async": ("async def", "await"),
    "py-django-n1": (".objects", "django"),
    "py-sqlalchemy-lazy": ("relationship", "sqlalchemy"),
    "py-fastapi-depends": ("Depends", "fastapi", "APIRouter"),
    "py-flask-routes": ("flask", "Flask", "Blueprint", ".route("),
    "py-flask-debug-true": ("debug=True", "flask", "Flask"),
    "py-flask-secret-key-literal": ("SECRET_KEY", "secret_key"),
    "py-manual-counter": (".get(",),
    "py-quadratic-list-concat": ("+ [",),
    "py-sort-in-loop": (".sort(", "sorted("),
    "py-pop0-queue": (".pop(0", ".pop( 0", ".pop(\t0"),
    "py-deepcopy-in-loop": ("deepcopy",),
    "py-frame-concat-in-loop": ("pd.concat", "np.concatenate", "np.vstack", "np.hstack"),
}


def applicable_idiom_detectors(scanned_text: str):
    """Yield ``(task_id, way, fn)`` for the detectors that COULD fire on
    ``scanned_text`` — i.e. those whose trigger token is present, plus every
    detector that declares no trigger (generic patterns). Lets a caller skip the
    framework/library detectors that can't possibly apply to the change."""
    for task_id, way, fn in PYTHON_IDIOM_DETECTORS:
        trig = _IDIOM_TRIGGERS.get(task_id)
        if trig is None or any(t in scanned_text for t in trig):
            yield task_id, way, fn
