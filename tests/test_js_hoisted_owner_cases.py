"""Persisted parent and call-edge controls for JavaScript and TypeScript owners."""

import sqlite3
from contextlib import closing

import pytest


def _symbol(db, path, name, kind, line):
    rows = db.execute(
        "SELECT s.id, s.parent_id FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE f.path = ? AND s.qualified_name = ? AND s.kind = ? AND s.line_start = ?",
        (path, name, kind, line),
    ).fetchall()
    assert len(rows) == 1, rows
    return rows[0]


def _calls_at(db, path, line):
    return db.execute(
        "SELECT e.source_id, e.target_id FROM edges e JOIN files f ON f.id = e.source_file_id "
        "WHERE e.kind = 'call' AND f.path = ? AND e.line = ?",
        (path, line),
    ).fetchall()


@pytest.mark.parametrize("type_declaration", ["interface Foo { x(): void }", "type Foo = { x: string };"])
def test_type_declaration_before_hoisted_assignment(project_factory, type_declaration):
    project = project_factory(
        {
            "app.ts": f"""{type_declaration}
Foo.bar = function () {{}};
Foo.prototype.m = function () {{}};
function Foo() {{}}
function use() {{
    Foo.bar();
    const f = new Foo();
    f.m();
    Foo.m();
    f.bar();
}}
"""
        }
    )
    path = "app.ts"
    with closing(sqlite3.connect(project / ".roam/index.db")) as db:
        owner, _ = _symbol(db, path, "Foo", "function", 4)
        static, static_parent = _symbol(db, path, "Foo.bar", "function", 2)
        instance, instance_parent = _symbol(db, path, "Foo.m", "function", 3)
        caller, _ = _symbol(db, path, "use", "function", 5)
        assert (static_parent, instance_parent) == (owner, owner)
        assert _calls_at(db, path, 6) == [(caller, static)]
        assert _calls_at(db, path, 8) == [(caller, instance)]
        assert _calls_at(db, path, 9) == []
        assert _calls_at(db, path, 10) == []


def test_value_import_construction_uses_merged_interface(project_factory):
    project = project_factory(
        {
            "merge.ts": """export class Foo { m() {} }
export interface Foo { n(): void }
""",
            "app.ts": """import { Foo } from './merge';
function use() {
    const f = new Foo();
    f.m();
    f.n();
    Foo.n();
}
""",
        }
    )
    with closing(sqlite3.connect(project / ".roam/index.db")) as db:
        cls, _ = _symbol(db, "merge.ts", "Foo", "class", 1)
        interface, _ = _symbol(db, "merge.ts", "Foo", "interface", 2)
        method, method_parent = _symbol(db, "merge.ts", "Foo.m", "method", 1)
        member, member_parent = _symbol(db, "merge.ts", "Foo.n", "method", 2)
        caller, _ = _symbol(db, "app.ts", "use", "function", 2)
        assert (method_parent, member_parent) == (cls, interface)
        assert _calls_at(db, "app.ts", 4) == [(caller, method)]
        assert _calls_at(db, "app.ts", 5) == [(caller, member)]
        assert _calls_at(db, "app.ts", 6) == []


@pytest.mark.parametrize("type_declaration", ["// No type declaration", "interface Foo { x(): void }"])
def test_ambiguous_hoisted_assignment_has_no_parent_or_call(project_factory, type_declaration):
    project = project_factory(
        {
            "app.ts": f"""{type_declaration}
Foo.bar = function () {{}};
Foo.prototype.m = function () {{}};
Solo.bar = function () {{}};
Solo.prototype.m = function () {{}};
function Foo() {{}}
function Foo() {{}}
function Solo() {{}}
function use() {{
    const f = new Foo();
    Foo.bar();
    f.m();
    const s = new Solo();
    Solo.bar();
    s.m();
}}
"""
        }
    )
    path = "app.ts"
    with closing(sqlite3.connect(project / ".roam/index.db")) as db:
        _symbol(db, path, "Foo", "function", 6)
        _symbol(db, path, "Foo", "function", 7)
        _, ambiguous_static_parent = _symbol(db, path, "Foo.bar", "function", 2)
        _, ambiguous_instance_parent = _symbol(db, path, "Foo.m", "function", 3)
        owner, _ = _symbol(db, path, "Solo", "function", 8)
        static, static_parent = _symbol(db, path, "Solo.bar", "function", 4)
        instance, instance_parent = _symbol(db, path, "Solo.m", "function", 5)
        caller, _ = _symbol(db, path, "use", "function", 9)
        assert (static_parent, instance_parent) == (owner, owner)
        assert _calls_at(db, path, 14) == [(caller, static)]
        assert _calls_at(db, path, 15) == [(caller, instance)]
        assert (
            ambiguous_static_parent,
            ambiguous_instance_parent,
            _calls_at(db, path, 11),
            _calls_at(db, path, 12),
        ) == (None, None, [], [])
