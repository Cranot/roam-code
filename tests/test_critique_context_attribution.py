"""F1 regression — ``critique`` impact/changed-symbol attribution must key off
lines a hunk *actually* added or deleted, never unchanged **context** lines.

Every fixture below is a minimal, self-contained extract of a real hunk from
third-party library repositories (express, requests, zod, fastapi). Each of
the four false positives shared ONE root cause: ``impact`` attributed
"change" to a symbol that appeared only as a
context line in the same hunk as the real edit. F1 fixes the diff→symbol
mapping (``parse_diff`` now records ``changed_lines``; ``find_changed_symbols``
only attributes symbols overlapping those lines).

The fastapi ``APIRouter`` case is the paired TRUE positive: the commit really
does modify logic inside ``class APIRouter``, so it must SURVIVE the fix.
"""

from __future__ import annotations

import sqlite3

from roam.critique.checks import find_changed_symbols, parse_diff


def _make_conn(symbols: list[tuple]) -> sqlite3.Connection:
    """Build a minimal in-memory index.

    ``symbols`` rows are ``(id, name, qualified_name, kind, file_path,
    line_start, line_end)``; the file table is derived from the distinct
    paths.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute(
        "CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT, qualified_name TEXT, "
        "kind TEXT, file_id INTEGER, line_start INTEGER, line_end INTEGER)"
    )
    paths: dict[str, int] = {}
    for _sid, _name, _q, _kind, path, _ls, _le in symbols:
        if path not in paths:
            fid = len(paths) + 1
            paths[path] = fid
            conn.execute("INSERT INTO files (id, path) VALUES (?, ?)", (fid, path))
    for sid, name, qname, kind, path, ls, le in symbols:
        conn.execute(
            "INSERT INTO symbols (id, name, qualified_name, kind, file_id, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, name, qname, kind, paths[path], ls, le),
        )
    conn.commit()
    return conn


def _names(conn: sqlite3.Connection, diff: str) -> set[str]:
    regions = parse_diff(diff)
    return {s.name for s in find_changed_symbols(conn, regions)}


def test_express_send_context_line_not_attributed() -> None:
    # express 59e205a — `var send = require('send')` is a context line; the
    # only add in the hunk is `var basename = path.basename;` at line 34.
    diff = (
        "--- a/lib/response.js\n"
        "+++ b/lib/response.js\n"
        "@@ -31,6 +31,7 @@ var cookie = require('cookie');\n"
        " var send = require('send');\n"
        " var extname = path.extname;\n"
        " var resolve = path.resolve;\n"
        "+var basename = path.basename;\n"
        " var vary = require('vary');\n"
        " const { Buffer } = require('node:buffer');\n"
    )
    conn = _make_conn(
        [
            (298, "send", "send", "variable", "lib/response.js", 31, 31),
            (299, "basename", "basename", "variable", "lib/response.js", 34, 34),
        ]
    )
    names = _names(conn, diff)
    assert "send" not in names  # the false positive is gone
    assert "basename" in names  # the real edit is attributed


def test_requests_httpbin_trailing_context_not_attributed() -> None:
    # requests 25340eb — the whole diff inserts `clean_proxy_environ` above the
    # unchanged `httpbin` fixture; `httpbin` is trailing context only.
    diff = (
        "--- a/tests/conftest.py\n"
        "+++ b/tests/conftest.py\n"
        "@@ -22,6 +22,15 @@ def prepare_url(value):\n"
        "     return inner\n"
        " \n"
        " \n"
        "+@pytest.fixture(autouse=True)\n"
        "+def clean_proxy_environ(monkeypatch):\n"
        '+    """Remove proxy related environment variables for every test."""\n'
        '+    proxy_vars = ("http_proxy", "https_proxy", "no_proxy", "ftp_proxy", "all_proxy")\n'
        "+    for var in proxy_vars:\n"
        "+        monkeypatch.delenv(var, raising=False)\n"
        "+        monkeypatch.delenv(var.upper(), raising=False)\n"
        "+\n"
        "+\n"
        " @pytest.fixture\n"
        " def httpbin(httpbin):\n"
        "     return prepare_url(httpbin)\n"
    )
    conn = _make_conn(
        [
            (400, "clean_proxy_environ", "clean_proxy_environ", "function", "tests/conftest.py", 25, 31),
            (525, "httpbin", "httpbin", "function", "tests/conftest.py", 34, 36),
        ]
    )
    names = _names(conn, diff)
    assert "httpbin" not in names
    assert "clean_proxy_environ" in names


def test_zod_pipe_context_line_not_attributed() -> None:
    # zod 02c2baf — the hunk inserts a `ZodPreprocess` block; `pipe` (below the
    # insertion) is untouched and appears only as context.
    diff = (
        "--- a/packages/zod/src/v4/classic/schemas.ts\n"
        "+++ b/packages/zod/src/v4/classic/schemas.ts\n"
        "@@ -2361,3 +2361,7 @@ export function invertCodec() {\n"
        "   }) as any;\n"
        " }\n"
        " \n"
        "+// ZodPreprocess\n"
        "+export const ZodPreprocess = core.constructor();\n"
        "+\n"
        "+// ZodReadonly\n"
        " export function pipe(in_, out) {\n"
        "   return new ZodPipe();\n"
        " }\n"
    )
    # pipe sits at new-side line 2368 (after the 4 inserted lines) — a context
    # line, so it must not be attributed. ZodPreprocess (line 2365) is the add.
    conn = _make_conn(
        [
            (2000, "ZodPreprocess", "ZodPreprocess", "variable", "packages/zod/src/v4/classic/schemas.ts", 2365, 2365),
            (2112, "pipe", "pipe", "function", "packages/zod/src/v4/classic/schemas.ts", 2368, 2370),
        ]
    )
    names = _names(conn, diff)
    assert "pipe" not in names
    assert "ZodPreprocess" in names


def test_fastapi_write_file_helper_not_attributed_but_apirouter_survives() -> None:
    # fastapi 319be50 — TWO findings, opposite verdicts:
    #  * write_file (3-line test helper) is FALSE: the add is record_dependency
    #    right after it; write_file's own body is context.
    #  * APIRouter is TRUE: the commit adds logic inside the class body.
    write_file_diff = (
        "--- a/tests/test_frontend.py\n"
        "+++ b/tests/test_frontend.py\n"
        "@@ -18,3 +19,10 @@ def write_file(path: Path, content: str) -> None:\n"
        "     path.write_text(content)\n"
        " \n"
        " \n"
        "+def record_dependency(calls: list[str], name: str):\n"
        "+    def dependency() -> None:\n"
        "+        calls.append(name)\n"
        "+\n"
        "+    return dependency\n"
        "+\n"
        "+\n"
    )
    conn = _make_conn(
        [
            (5157, "write_file", "write_file", "function", "tests/test_frontend.py", 17, 20),
            (5158, "record_dependency", "record_dependency", "function", "tests/test_frontend.py", 22, 26),
        ]
    )
    names = _names(conn, write_file_diff)
    assert "write_file" not in names
    assert "record_dependency" in names

    # APIRouter: an added line lands inside the class body (2652..) — TP kept.
    apirouter_diff = (
        "--- a/fastapi/routing.py\n"
        "+++ b/fastapi/routing.py\n"
        "@@ -2515,4 +2652,7 @@ class APIRouter(routing.Router):\n"
        "         normalized_path = _normalize_frontend_path(path)\n"
        "         if self._frontend_routes is None:\n"
        "-            self._frontend_routes = _FrontendRouteGroup()\n"
        "+            self._frontend_routes = _FrontendRouteGroup(\n"
        "+                dependencies=self.dependencies,\n"
        "+            )\n"
        "             self._low_priority_routes.append(self._frontend_routes)\n"
    )
    conn2 = _make_conn(
        [
            (3520, "APIRouter", "APIRouter", "class", "fastapi/routing.py", 2210, 2820),
        ]
    )
    assert "APIRouter" in _names(conn2, apirouter_diff)


def test_deletion_inside_symbol_is_attributed() -> None:
    # A pure-deletion inside a symbol body must still attribute (the removed
    # lines were part of that symbol).
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -10,5 +10,3 @@ def foo():\n     a = 1\n-    b = 2\n-    c = 3\n     return a\n"
    )
    conn = _make_conn([(1, "foo", "foo", "function", "mod.py", 9, 12)])
    assert "foo" in _names(conn, diff)


# --- F2: test-only symbols get demoted impact severity --------------------
def test_f2_test_only_symbol_impact_demoted() -> None:
    """A high-blast symbol in a test file is demoted (its callers are tests)."""
    import sqlite3

    from roam.critique.checks import ChangedSymbol, check_impact

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE edges (source_id INTEGER, target_id INTEGER, kind TEXT)")
    conn.execute(
        "CREATE TABLE runtime_stats (symbol_id INTEGER, call_count INTEGER, p99_latency_ms REAL, error_rate REAL)"
    )
    # 25 callers of the test helper (target_id=100)
    for src in range(1, 26):
        conn.execute("INSERT INTO edges (source_id, target_id, kind) VALUES (?, 100, 'call')", (src,))
    conn.commit()

    test_sym = ChangedSymbol(100, "write_file", "write_file", "function", "tests/test_frontend.py", 17, 19)
    prod_sym = ChangedSymbol(100, "handler", "handler", "function", "src/app.py", 10, 40)

    tf = check_impact(conn, [test_sym], high_callers=10)
    assert len(tf) == 1
    assert tf[0].evidence["test_only"] is True
    # 25 callers would be "high" for a prod symbol; demoted to "medium" here.
    assert tf[0].severity == "medium"

    pf = check_impact(conn, [prod_sym], high_callers=10)
    assert pf[0].evidence["test_only"] is False
    assert pf[0].severity == "high"
