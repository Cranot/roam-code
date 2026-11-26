"""Exact persisted edge controls for JS/TS owner merges, overloads and module order."""

import sqlite3

import pytest

from tests.test_js_edge_resolution import calls


@pytest.mark.parametrize(
    ("caller", "target"),
    [
        ("construct", "Foo.m"),
        ("typed", "Foo.m"),
        ("extra", "Foo.n"),
        ("Foo.inside", "Foo.m"),
        ("value", "Cfg.make"),
        ("annotation", "Cfg.load"),
        ("imported", "Foo.m"),
    ],
)
def test_M2(project_factory, caller, target):
    path = "app.ts" if caller == "imported" else "merge.ts"
    project = project_factory(
        {
            "merge.ts": """
export class Foo { m() {} inside() { this.m(); } }
export interface Foo { n(): void }
export interface Cfg { load(): void }
export const Cfg = { make() {} };
function construct() { const f = new Foo(); f.m(); }
function typed(f: Foo) { f.m(); }
function extra(f: Foo) { f.n(); }
function value() { Cfg.make(); }
function annotation(c: Cfg) { c.load(); }
function wrongType(c: Cfg) { c.make(); }
function wrongValue() { Cfg.load(); }
function wrongStatic() { Foo.n(); }
function control() { const solo = new Solo(); solo.valid(); }
class Solo { valid() {} }
""",
            "app.ts": "import { Foo } from './merge'; function imported(f: Foo) { f.m(); }",
            "ambiguous.ts": """
export class Dupe { m() {} }
export class Dupe { m() {} }
function ambiguous(x: Dupe) { x.m(); }
""",
        }
    )
    edges = calls(project)
    assert ("merge.ts", "control", "merge.ts", "Solo.valid") in edges, edges
    assert not {e for e in edges if e[1] in {"wrongType", "wrongValue", "wrongStatic", "ambiguous"}}, edges
    assert (path, caller, "merge.ts", target) in edges, edges


@pytest.mark.parametrize(
    ("caller", "target", "line"),
    [
        ("instance", "Repo.find", 4),
        ("staticUse", "Repo.of", 6),
        ("declareOnly", "Api.get", 9),
        ("abstractUse", "Abstract.use", 13),
    ],
)
def test_M3(project_factory, caller, target, line):
    project = project_factory(
        {
            "defs.ts": """export class Repo {
  find(a: string): void;
  find(a: number): void;
  find(a: any) {}
  static of(a: string): Repo;
  static of(a: any): Repo { return new Repo(); }
}
export interface Api {
  get(a: string): void;
  get(a: number): void;
}
export abstract class Abstract {
  abstract use(a: string): void;
  abstract use(a: number): void;
}
export class Duplicate {
  run() {}
  run() {}
}
""",
            "app.ts": """import { Abstract, Api, Duplicate, Repo } from './defs';
function instance(r: Repo) { r.find(1); }
function staticUse() { Repo.of('x'); }
function declareOnly(a: Api) { a.get(1); }
function abstractUse(a: Abstract) { a.use(1); }
function unknown(a: unknown) { a.get(1); }
function wrongMode() { Repo.find(1); }
function duplicate(a: Duplicate) { a.run(); }
function control() { Repo.of; const r = new Plain(); r.work(); }
class Plain { work() {} }
""",
        }
    )
    edges = calls(project)
    assert ("app.ts", "control", "app.ts", "Plain.work") in edges, edges
    assert not {e for e in edges if e[1] in {"unknown", "wrongMode", "duplicate"}}, edges
    with sqlite3.connect(project / ".roam/index.db") as db:
        rows = db.execute(
            "SELECT sf.path,s.qualified_name,tf.path,t.qualified_name,t.line_start FROM edges e "
            "JOIN symbols s ON s.id=e.source_id JOIN symbols t ON t.id=e.target_id "
            "JOIN files sf ON sf.id=s.file_id JOIN files tf ON tf.id=t.file_id "
            "WHERE e.kind='call' AND s.qualified_name=?",
            (caller,),
        ).fetchall()
    assert rows == [("app.ts", caller, "defs.ts", target, line)], rows


@pytest.mark.parametrize(("caller", "target"), [("staticUse", "Foo.baz"), ("instanceUse", "Foo.qux")])
def test_m1(project_factory, caller, target):
    project = project_factory(
        {
            "app.js": """
function Foo() {}
Foo.baz = function () {};
Foo.prototype.qux = function () {};
function staticUse() { Foo.baz(); }
function instanceUse() { const f = new Foo(); f.qux(); }
function wrongStatic() { Foo.qux(); }
function wrongInstance() { const f = new Foo(); f.baz(); }
function shadowed(Foo) { Foo.baz(); }
function unknown(f) { f.qux(); }
function control() { const x = new Plain(); x.work(); }
class Plain { work() {} }
"""
        }
    )
    edges = calls(project)
    assert ("app.js", "control", "app.js", "Plain.work") in edges, edges
    assert not {
        e for e in edges if e[1] in {"wrongStatic", "wrongInstance", "shadowed", "unknown"} and e[3].startswith("Foo.")
    }, edges
    assert ("app.js", caller, "app.js", target) in edges, edges


def test_m2(project_factory):
    project = project_factory(
        {
            "app.js": """
Foo.prototype.bar = function () {};
function Foo() {}
Missing.prototype.bar = function () {};
function use() { const f = new Foo(); f.bar(); }
function noOwner(Missing) { const f = new Missing(); f.bar(); }
function control() { const x = new Plain(); x.bar(); }
class Plain { bar() {} }
"""
        }
    )
    edges = calls(project)
    assert ("app.js", "control", "app.js", "Plain.bar") in edges, edges
    assert not any(e[1] == "noOwner" and e[3].endswith(".bar") for e in edges), edges
    with sqlite3.connect(project / ".roam/index.db") as db:
        rows = db.execute(
            "SELECT m.qualified_name,p.qualified_name FROM symbols m LEFT JOIN symbols p ON p.id=m.parent_id "
            "WHERE m.qualified_name IN ('Foo.bar', 'Missing.bar', 'Plain.bar') ORDER BY m.qualified_name"
        ).fetchall()
    assert rows == [("Foo.bar", "Foo"), ("Missing.bar", None), ("Plain.bar", "Plain")], rows
    assert ("app.js", "use", "app.js", "Foo.bar") in edges, edges


@pytest.mark.parametrize(
    ("caller", "target"),
    [
        ("Svc.run", "Repo.find"),
        ("nested", "Repo.find"),
        ("Qualified.run", "Repo.find"),
        ("Constructor.run", "Repo.find"),
    ],
)
def test_m3(project_factory, caller, target):
    project = project_factory(
        {
            "lib.ts": "export class Repo { find() {} }",
            "app.ts": """
import { Repo } from './lib';
import * as Lib from './lib';
class Svc { repo: Repo; run(Repo: unknown) { this.repo.find(); } }
class Qualified {
  repo: Lib.Repo;
  run(Lib: unknown) { this.repo.find(); }
}
class Constructor {
  constructor(readonly repo: Repo) {}
  run(Repo: unknown) { this.repo.find(); }
}
function nested() {
  const r = new Repo();
  { const Repo = 1; r.find(); }
}
function shadowed(Repo: any) { const r = new Repo(); r.find(); }
function qualifiedShadowed<Lib>(r: Lib.Repo) { r.find(); }
function control(r: Repo) { r.find(); }
""",
        }
    )
    edges = calls(project)
    assert ("app.ts", "control", "lib.ts", "Repo.find") in edges, edges
    assert not {e for e in edges if e[1] in {"shadowed", "qualifiedShadowed"} and e[3] == target}, edges
    assert ("app.ts", caller, "lib.ts", target) in edges, edges


@pytest.mark.parametrize(
    ("module", "preferred", "fallback"),
    [
        ("dup", "dup.ts", "dup/index.ts"),
        ("dup.js", "dup.d.ts", "dup.js"),
    ],
)
def test_m4(project_factory, module, preferred, fallback):
    project = project_factory(
        {
            preferred: "export class Dup { m() {} }".replace(
                "m() {}", "m(): void;" if preferred.endswith(".d.ts") else "m() {}"
            ),
            fallback: "export class Dup { m() {} onlyFallback() {} }",
            "single/index.ts": "export class Single { valid() {} }",
            "app.ts": f"""import {{ Dup }} from './{module}';
import {{ Single }} from './single';
function use(d: Dup) {{ d.m(); }}
function bad(d: Dup) {{ d.onlyFallback(); }}
function control(s: Single) {{ s.valid(); }}
""",
        }
    )
    edges = calls(project)
    assert ("app.ts", "control", "single/index.ts", "Single.valid") in edges, edges
    assert not {e for e in edges if e[1] == "bad" or e[2] == fallback}, edges
    assert ("app.ts", "use", preferred, "Dup.m") in edges, edges
