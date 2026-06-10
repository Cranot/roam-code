"""TS/JS side-effect detection — the per-symbol classifier and the module-init
scan must recognise JavaScript/TypeScript I/O sinks (fetch, bun:sqlite, Bun.serve,
timers), not only the Python sink set.

Regression for the audit-benchmark finding: a TS webhook service had every symbol
classified ``none`` (missing every DB write + the network egress), because the
sink-pattern table and pre-filter were Python-only.
"""

from __future__ import annotations

from roam.world_model.side_effects import (
    _classify_one_symbol,
    scan_module_init_effects,
)


def _kinds(body: str) -> set[str]:
    kinds, _ev, _conf = _classify_one_symbol(body, [], set())
    return set(kinds)


def test_fetch_is_network_io_write():
    assert "io_write" in _kinds('const res = await fetch(sub.url, { method: "POST", body });')


def test_bun_sqlite_query_read_vs_write():
    assert "io_read" in _kinds('return db.query("SELECT * FROM deliveries WHERE id = ?").get(id);')
    assert "io_write" in _kinds('db.query("UPDATE deliveries SET status = ? WHERE id = ?").run(s, id);')
    assert "io_write" in _kinds('db.query("INSERT INTO events VALUES (?)").run(id);')


def test_template_literal_sql_classifies():
    assert "io_read" in _kinds("return db.query(`SELECT * FROM deliveries WHERE ${w}`).all();")


def test_setinterval_is_process():
    assert "process" in _kinds("return setInterval(() => { void tick(); }, intervalMs);")


def test_pure_ts_stays_none():
    # an HMAC helper with no I/O must stay pure
    body = 'return createHmac("sha256", secret).update(body).digest("hex");'
    assert _kinds(body) in ({"none"}, set())


def test_python_regression_still_classifies():
    # the Python sink set must be unaffected by the TS additions
    assert "io_write" in _kinds("requests.post(url, json=payload)")
    assert "io_read" in _kinds("data = requests.get(url).json()")


def test_module_init_detects_import_time_io():
    src = (
        'import { Database } from "bun:sqlite";\n'
        'export const db = new Database("x.db");\n'
        'db.exec("PRAGMA journal_mode = WAL");\n'
        "db.exec(`CREATE TABLE IF NOT EXISTS t (id TEXT)`);\n"
        "export function pure(a: number) {\n"
        "  return a + 1;\n"
        "}\n"
    )
    findings = scan_module_init_effects(src)
    kinds = {k for _ln, k, _lbl in findings}
    assert "io_write" in kinds  # the top-level db.exec(DDL)
    assert "io_read" in kinds  # new Database at module scope
    # the indented function body must NOT contribute module-init findings
    assert all(ln <= 4 for ln, _k, _lbl in findings)


def test_module_init_clean_file_is_empty():
    src = 'import { foo } from "./foo";\nexport function handler(req: Request) {\n  return foo(req);\n}\n'
    assert scan_module_init_effects(src) == []


def test_module_init_skips_docstring_mentions():
    # a docstring that MENTIONS a sink is text, not an executing import-time call
    src = (
        '"""This module wraps requests.post and shutil.copy for callers.\n'
        "It must not introduce a new requests.post at import time.\n"
        '"""\n'
        "def helper(x):\n"
        "    return x\n"
    )
    assert scan_module_init_effects(src) == []


def test_module_init_python_top_level_io_flagged():
    # a REAL top-level call (after the docstring closes) must be flagged
    src = (
        '"""docstring mentioning requests.post harmlessly."""\n'
        "import requests\n"
        'requests.post("http://x", json={})\n'
        "def helper():\n"
        "    return 1\n"
    )
    kinds = {k for _ln, k, _lbl in scan_module_init_effects(src)}
    assert "io_write" in kinds


def test_module_init_backtick_sql_opener_then_skips_body():
    # the .exec(`...`) opener line is real code (flag it); the SQL body lines
    # inside the template literal are skipped
    src = (
        'import { Database } from "bun:sqlite";\n'
        'export const db = new Database("x.db");\n'
        "db.exec(`\n"
        "  CREATE TABLE t (id TEXT);\n"
        "  INSERT INTO t VALUES ('a');\n"
        "`);\n"
        "export function f() { return 1; }\n"
    )
    findings = scan_module_init_effects(src)
    kinds = {k for _ln, k, _lbl in findings}
    assert "io_write" in kinds  # the db.exec(` opener
    # the CREATE/INSERT lines inside the backtick block must NOT each add a finding
    assert len([1 for _ln, k, _lbl in findings if k == "io_write"]) == 1
