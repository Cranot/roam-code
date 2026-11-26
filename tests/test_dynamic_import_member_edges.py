"""Dynamic import receivers resolve through the imported module's exports."""

import sqlite3
from contextlib import closing

import pytest


@pytest.mark.parametrize(
    ("body", "target"),
    [
        pytest.param("return import('@/utils/x').then(m => m.f());", "src/utils/x.ts", id="then"),
        pytest.param("return (await import('./utils/x.js')).f();", "src/utils/x.ts", id="await-expression"),
        pytest.param("const m = await import('./utils/x'); return m.f();", "src/utils/x.ts", id="await-const"),
        pytest.param(
            "return import('./utils/x').then(function (m) { return m.f(); });",
            "src/utils/x.ts",
            id="then-function",
        ),
        pytest.param(
            "return import('./utils/x').then(m => { { let m = other; m = {}; } return m.f(); });",
            "src/utils/x.ts",
            id="shadowed-write",
        ),
        pytest.param(
            "return import('./utils/x').then(m => { m = other; return m.f(); });",
            None,
            id="reassigned-parameter",
        ),
        pytest.param(
            "return import('./utils/x').then(m => { [m] = other; return m.f(); });",
            None,
            id="destructuring-write",
        ),
        pytest.param("return import('./utils/x').then((_, m) => m.f());", None, id="second-parameter"),
        pytest.param("return other.then(m => m.f());", None, id="other-promise"),
        pytest.param("let m = await import('./utils/x'); return m.f();", None, id="mutable-binding"),
        pytest.param("return (await import('./private')).f();", None, id="unexported-member"),
        pytest.param("return import(path).then(m => m.f());", None, id="nonliteral-specifier"),
    ],
)
def test_dynamic_import_member_edge(project_factory, body, target):
    project = project_factory(
        {
            "src/app.ts": f"export async function use(path: string, other: any) {{\n  {body}\n}}\n",
            "src/utils/x.ts": "export function f() {}\n",
            "src/decoy.ts": "export function f() {}\n",
            "src/private.ts": "function f() {}\nexport function g() {}\n",
        }
    )
    with closing(sqlite3.connect(project / ".roam/index.db")) as db:
        edges = db.execute(
            """
            SELECT sf.path, s.qualified_name, s.line_start, e.kind, e.line,
                   tf.path, t.qualified_name, t.line_start
            FROM edges e
            JOIN files ref ON ref.id = e.source_file_id
            JOIN symbols s ON s.id = e.source_id
            JOIN files sf ON sf.id = s.file_id
            JOIN symbols t ON t.id = e.target_id
            JOIN files tf ON tf.id = t.file_id
            WHERE ref.path = 'src/app.ts' AND t.name = 'f'
            """
        ).fetchall()
    assert edges == ([("src/app.ts", "use", 1, "call", 2, target, "f", 1)] if target else [])
