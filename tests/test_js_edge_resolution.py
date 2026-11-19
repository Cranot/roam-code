"""Exact persisted edges for JS/TS owner resolution."""

import sqlite3

import pytest

from tests.conftest import index_in_process


def _calls(project):
    with sqlite3.connect(project / ".roam/index.db") as db:
        return set(
            db.execute(
                "SELECT sf.path,s.qualified_name,tf.path,t.qualified_name FROM edges e "
                "JOIN symbols s ON s.id=e.source_id JOIN symbols t ON t.id=e.target_id "
                "JOIN files sf ON sf.id=s.file_id JOIN files tf ON tf.id=t.file_id WHERE e.kind='call'"
            )
        )


def calls(project):
    before = _calls(project)
    # Touch an unrelated file: every existing caller/target is merged from
    # persisted symbols. Static/instance evidence must survive that route too.
    (project / "_touch.ts").write_text("export const marker = 1;")
    output, code = index_in_process(project)
    assert code == 0, output
    assert _calls(project) == before, (_calls(project), before)
    return before


@pytest.mark.parametrize("import_get", ["import get from 'lodash/get';", ""])
@pytest.mark.parametrize("member", ["get", "constructor"])
def test_defect_1(project_factory, import_get, member):
    p = project_factory(
        {
            "api.ts": f"export const api = {{ {member}() {{}} }};",
            "app.ts": import_get
            + " import { api } from './api';\nexport function bad() { get(o, 'a') }\nexport function good() { api.get() }",
        }
    )
    if member != "get":
        # The object method named constructor is still an object method,
        # even though the extractor classifies its symbol as a constructor.
        (p / "app.ts").write_text((p / "app.ts").read_text().replace("get", member))
        output, code = index_in_process(p)
        assert code == 0, output
    c = calls(p)
    assert ("app.ts", "good", "api.ts", f"api.{member}") in c, c
    assert ("app.ts", "bad", "api.ts", f"api.{member}") not in c, c


@pytest.mark.parametrize(
    "bad_body",
    [
        "static bad() { this.work() }",
        "nested() { const obj = { run() { this.work() } }; }",
        "nested() { class Inner { static { this.work() } } }",
    ],
)
@pytest.mark.parametrize("extension", ["ts", "js"])
def test_defect_2(project_factory, bad_body, extension):
    file = "app." + extension
    p = project_factory(
        {
            file: """class C {
 work() {}
 static boot() {}
 good() { this.work() }
 static goodStatic() { this.boot() }
 badInstance() { this.boot() }
"""
            + bad_body
            + "\n}"
        }
    )
    c = calls(p)
    assert (file, "C.good", file, "C.work") in c, c
    assert (file, "C.goodStatic", file, "C.boot") in c, c
    assert not [e for e in c if e[1] not in {"C.good", "C.goodStatic"} and e[3] in {"C.work", "C.boot"}], c


@pytest.mark.parametrize(
    "declaration",
    [
        "private users: UserService;",
        "constructor(private users: UserService) {}",
        "constructor(readonly users: UserService) {}",
    ],
)
@pytest.mark.parametrize("service", ["class UserService { findAll() {} }", "interface UserService { findAll(): void }"])
def test_defect_3(project_factory, declaration, service):
    p = project_factory(
        {
            "app.ts": f"""{service}
class Other {{ findAll() {{}} }}
class C {{ {declaration}
 good() {{ this.users.findAll() }}
 bad() {{ this.unknown.findAll() }}
 ambiguous: UserService | Other;
 badUnion() {{ this.ambiguous.findAll() }}
}}"""
        }
    )
    c = calls(p)
    assert ("app.ts", "C.good", "app.ts", "UserService.findAll") in c, c
    assert not [e for e in c if e[1] in {"C.bad", "C.badUnion"} and e[3].endswith("findAll")], c
    assert ("app.ts", "C.good", "app.ts", "Other.findAll") not in c, c


@pytest.mark.parametrize("form", ["default", "export-equals", "barrel", "null", "undefined"])
def test_defect_4(project_factory, form):
    files = {"a.ts": "export class Bar { work() {} }", "other.ts": "export class Other { work() {} }"}
    if form == "default":
        files["a.ts"] = "export default class Bar { work() {} }"
        header, value = "import Foo from './a';", "const x = new Foo();"
    elif form == "export-equals":
        files["a.ts"] = "class Bar { work() {} }; export = Bar;"
        header, value = "import Foo = require('./a');", "const x = new Foo();"
    elif form == "barrel":
        files["a.ts"] = "export function work() {}"
        files["barrel.ts"] = "export * from './a';"
        header, value = "import * as x from './barrel';", ""
    else:
        header, value = "import { Bar } from './a';", f"const x: Bar | {form} = injected;"
    files["app.ts"] = (
        header + f"\nexport function good() {{ {value} x.work() }}\nexport function bad(x: unknown) {{ x.work() }}"
    )
    p = project_factory(files)
    c = calls(p)
    target = "work" if form == "barrel" else "Bar.work"
    assert ("app.ts", "good", "a.ts", target) in c, c
    assert not [e for e in c if e[1] == "bad" and e[3].endswith("work")], c
    assert ("app.ts", "good", "other.ts", "Other.work") not in c, c


def test_defect_5(project_factory):
    p = project_factory(
        {
            "app.ts": """class Owner { work() {} }
class Owner { other() {} }
function bad(x: Owner) { x.work() }
class Unique { work() {} }
function good(x: Unique) { x.work() }
"""
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "app.ts", "Unique.work") in c, c
    assert ("app.ts", "bad", "app.ts", "Owner.work") not in c, c


@pytest.mark.parametrize("form", ["default", "export-equals"])
def test_defect_4_constructor_target(project_factory, form):
    export = "export default class Bar {}" if form == "default" else "class Bar {}; export = Bar;"
    imp = "import Foo from './a';" if form == "default" else "import Foo = require('./a');"
    p = project_factory(
        {
            "a.ts": export,
            "app.ts": imp + "\nfunction good() { new Foo() }\nfunction bad(Foo) { new Foo() }",
            "decoy.ts": "export class Foo {}",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "a.ts", "Bar") in c, c
    assert not [e for e in c if e[1] == "bad"], c
    assert ("app.ts", "good", "decoy.ts", "Foo") not in c, c


def test_defect_5_star_ambiguity(project_factory):
    p = project_factory(
        {
            "a.ts": "export function work() {}",
            "b.ts": "export function work() {}",
            "barrel.ts": "export * from './a'; export * from './b';",
            "app.ts": "import * as good from './a'; import * as bad from './barrel';\nfunction goodCall() { good.work() }\nfunction badCall() { bad.work() }",
        }
    )
    # Different source symbols keep graph deduplication from hiding an
    # incorrectly resolved bad.work behind the supported good.work edge.
    with sqlite3.connect(p / ".roam/index.db") as db:
        rows = db.execute(
            "SELECT s.qualified_name,t.qualified_name,f.path,e.line FROM edges e JOIN symbols s ON s.id=e.source_id JOIN symbols t ON t.id=e.target_id JOIN files f ON f.id=t.file_id WHERE e.kind='call'"
        ).fetchall()
    assert rows == [("goodCall", "work", "a.ts", 2)], rows


@pytest.mark.parametrize("nested", [False, True])
def test_defect_5_imported_owner_ambiguity(project_factory, nested):
    p = project_factory(
        {
            "a.ts": "export class Owner { work() {} }",
            "b.ts": "export class Owner { other() {} }",
            "barrel.ts": "export * from './a'; export * from './b';"
            if not nested
            else "export * from './ambiguous'; export * from './a';",
            "ambiguous.ts": "export { Owner } from './a'; export { Owner } from './b';",
            "app.ts": "import { Owner } from './barrel'; import { Owner as Unique } from './a';\n"
            "function bad(x: Owner) { x.work() }\nfunction good(x: Unique) { x.work() }",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "a.ts", "Owner.work") in c, c
    assert ("app.ts", "bad", "a.ts", "Owner.work") not in c, c


def test_defect_4_default_is_not_named_export(project_factory):
    p = project_factory(
        {
            "default.ts": "export default class Real { work() {} }",
            "named.ts": "export class Foo { work() {} }",
            "app.ts": "import Good from './default'; import Foo from './named';\n"
            "function good() { const x = new Good(); x.work() }\n"
            "function bad() { const x = new Foo(); x.work() }",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "default.ts", "Real.work") in c, c
    assert ("app.ts", "bad", "named.ts", "Foo.work") not in c, c


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_defect_2_arrow_field_controls(project_factory, extension):
    file = "app." + extension
    p = project_factory(
        {
            file: """class C {
 work() {}
 static boot() {}
 good = () => this.work();
 static goodStatic = () => this.boot();
 static bad = () => this.work();
 static callField() { this.goodStatic() }
 badField() { this.goodStatic() }
}"""
        }
    )
    c = calls(p)
    assert (file, "C.good", file, "C.work") in c, c
    assert (file, "C.goodStatic", file, "C.boot") in c, c
    assert (file, "C.bad", file, "C.work") not in c, c
    assert (file, "C.callField", file, "C.goodStatic") in c, c
    assert (file, "C.badField", file, "C.goodStatic") not in c, c


def test_defect_3_declared_type_must_be_class_or_interface(project_factory):
    p = project_factory(
        {
            "dep.ts": "const Alias = { findAll() {} }; type Alias = typeof Alias; export { Alias };\n"
            "export class Real { findAll() {} }",
            "app.ts": "import { Alias, Real } from './dep';\nclass C {\n"
            "private users: Alias; private real: Real;\n"
            "bad() { this.users.findAll() }\ngood() { this.real.findAll() }\n}",
        }
    )
    c = calls(p)
    assert ("app.ts", "C.good", "dep.ts", "Real.findAll") in c, c
    assert ("app.ts", "C.bad", "dep.ts", "Alias.findAll") not in c, c
