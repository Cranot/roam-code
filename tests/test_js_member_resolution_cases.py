"""Exact graph identities for JS/TS member resolution, with separate negative callers."""

import pytest

from tests.test_js_edge_resolution import calls


def test_B2(project_factory):
    p = project_factory(
        {
            "foo.h": "int add(int a, int b);",
            "main.cpp": '#include "foo.h"\nint main() { return add(1, 2); }',
            "other.py": "def add(): pass",
            "bad.ts": "function bad() { add() }",
        }
    )
    c = calls(p)
    assert ("main.cpp", "main", "foo.h", "add") in c, c
    assert not [e for e in c if e[0] == "bad.ts"], c


@pytest.mark.parametrize("imported", [False, True])
def test_M1(project_factory, imported):
    p = project_factory(
        {
            "src/a.ts": ("import { format } from '../lib/format';\n" if imported else "")
            + "export function f() { format() }",
            "src/format.py": "def format(): pass",
            "lib/format.ts": "export function format() {}",
            "bad.ts": "function bad() { foreign() }",
            "foreign.py": "def foreign(): pass",
        }
    )
    c = calls(p)
    assert ("src/a.ts", "f", "lib/format.ts", "format") in c, c
    assert not [e for e in c if e[0] == "bad.ts"], c


@pytest.mark.parametrize("receiver", ["new", "type", "class"])
def test_M3(project_factory, receiver):
    good = {
        "new": "function good() { const b = new Bar(); b.both() }",
        "type": "function good(b: Bar) { b.both() }",
        "class": "function good() { Bar.both() }",
    }[receiver]
    bad = {
        "new": "function bad() { const b = new Bar(); b.make() }",
        "type": "function bad(b: Bar) { b.make() }",
        "class": "function bad() { Bar.inst() }",
    }[receiver]
    p = project_factory(
        {"app.ts": "class Bar { both() {} static both() {} inst() {} static make() {} }\n" + good + "\n" + bad}
    )
    c = calls(p)
    # Compare target declaration lines too: duplicate qualified names differ by mode.
    import sqlite3

    with sqlite3.connect(p / ".roam/index.db") as db:
        rows = db.execute(
            "SELECT s.qualified_name,t.signature FROM edges e JOIN symbols s ON s.id=e.source_id JOIN symbols t ON t.id=e.target_id WHERE e.kind='call' AND t.name='both'"
        ).fetchall()
    assert ("app.ts", "good", "app.ts", "Bar.both") in c, c
    assert len(rows) == 1 and ("static" in rows[0][1].split()) == (receiver == "class"), rows
    assert not [e for e in c if e[1] == "bad" and e[3] in {"Bar.inst", "Bar.make"}], c


@pytest.mark.parametrize("form", ["object", "class", "generic"])
def test_M4(project_factory, form):
    outer = "const api = { get() { return 1 } };" if form == "object" else "class Foo { m() {} }"
    bad = {
        "object": "function bad() { const api = { get() { return 2 } }; api.get() }",
        "class": "function bad() { class Foo { m() {} } const x = new Foo(); x.m() }",
        "generic": "function bad<Foo>(x: Foo) { x.m() }",
    }[form]
    good = "function good() { api.get() }" if form == "object" else "function good(x: Foo) { x.m() }"
    p = project_factory({"app.ts": outer + "\n" + bad + "\n" + good})
    c = calls(p)
    target = "api.get" if form == "object" else "Foo.m"
    assert ("app.ts", "good", "app.ts", target) in c, c
    assert ("app.ts", "bad", "app.ts", target) not in c, c


@pytest.mark.parametrize("binding", ["const helper = () => 1;", "function helper() { return 1 }", "class helper {}"])
def test_M5(project_factory, binding):
    call = "new helper()" if binding.startswith("class") else "helper()"
    p = project_factory(
        {
            "app.ts": "export function bad() { "
            + binding
            + " return "
            + call
            + " }\nexport function good() { helper() }",
            "dep.ts": "export function helper() {}",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "dep.ts", "helper") in c, c
    assert ("app.ts", "bad", "dep.ts", "helper") not in c, c


@pytest.mark.parametrize("form", ["overload", "type-value"])
@pytest.mark.parametrize("barrel", [False, True])
def test_M6(project_factory, form, barrel):
    export = (
        "export function create(a: string): void;\nexport function create(a: number): void;\nexport function create(a: any) {}"
        if form == "overload"
        else "export const Config = {load(){}}; export type Config = typeof Config;"
    )
    imp = "import * as api from './barrel';" if form == "overload" else "import {Config} from './barrel';"
    expr, target = ("api.create(1)", "create") if form == "overload" else ("Config.load()", "Config.load")
    p = project_factory(
        {
            "api.ts": export,
            "barrel.ts": "export * from './api';" if barrel else export,
            "dup.ts": "export class Owner { work() {} }\nexport class Owner { other() {} }",
            "app.ts": imp
            + "\nimport { Owner } from './dup';\nfunction good() { "
            + expr
            + " }\nfunction bad(x: Owner) { x.work() }",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "api.ts" if barrel else "barrel.ts", target) in c, c
    assert not [e for e in c if e[1] == "bad" and e[3] == "Owner.work"], c


def test_m1(project_factory):
    p = project_factory(
        {
            "app.ts": "class Repo { find() {} }\nclass Svc { private repo = new Repo(); run() { this.repo.find() } }\nclass Bad { private repo = factory(); run() { this.repo.find() } }"
        }
    )
    c = calls(p)
    assert ("app.ts", "Svc.run", "app.ts", "Repo.find") in c, c
    assert ("app.ts", "Bad.run", "app.ts", "Repo.find") not in c, c


@pytest.mark.parametrize("kind", ["class", "interface"])
def test_m2(project_factory, kind):
    member = "m() {}" if kind == "class" else "m(): void"
    p = project_factory(
        {
            "app.ts": f"namespace NS {{ export {kind} Foo {{ {member} }} }}\nfunction good(x: NS.Foo) {{ x.m() }}\nfunction bad(x: Other.Foo) {{ x.m() }}"
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "app.ts", "NS.Foo.m") in c, c
    assert ("app.ts", "bad", "app.ts", "NS.Foo.m") not in c, c


def test_m3(project_factory):
    p = project_factory(
        {
            "x.ts": "export function x() {}",
            "app.ts": "import { x } from './x';\nexport function good(k: number) { x(); switch (k) { case 1: const x = 5; break; } }\nfunction bad(k: number) { switch(k) { case 1: const x = 5; x(); } }",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "x.ts", "x") in c, c
    assert ("app.ts", "bad", "x.ts", "x") not in c, c


@pytest.mark.parametrize("style", ["star", "named", "default"])
def test_m2_imported_namespace(project_factory, style):
    export = "export class Foo { m() {} }" if style == "star" else "export namespace NS { export class Foo { m() {} } }"
    if style == "default":
        export = "namespace NS { export class Foo { m() {} } }; export default NS;"
    imp = {
        "star": "import * as Alias from './dep';",
        "named": "import {NS as Alias} from './dep';",
        "default": "import Alias from './dep';",
    }[style]
    p = project_factory(
        {
            "dep.ts": export,
            "app.ts": imp + "\nfunction good(x: Alias.Foo) { x.m() }\nfunction bad<Alias>(x: Alias.Foo) { x.m() }",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "dep.ts", "Foo.m" if style == "star" else "NS.Foo.m") in c, c
    assert not [e for e in c if e[1] == "bad" and e[3].endswith(".m")], c


def test_language_pairs():
    from roam.index.parser import EXTENSION_MAP
    from roam.index.relations import _same_language_family
    from roam.languages.registry import get_supported_languages

    # Include the registry's supported IDs AND every extension-mapped ID,
    # including tier-2 grammars and aliases. Grammar reuse is not a bridge.
    representatives = {language: "file" + extension for extension, language in EXTENSION_MAP.items()}
    representatives["sfxml"] = "file.cls-meta.xml"
    assert set(get_supported_languages()) <= representatives.keys()
    groups = [{"javascript", "typescript", "tsx", "vue", "svelte"}, {"c", "cpp"}]
    for source, source_path in representatives.items():
        for target, target_path in representatives.items():
            expected = source == target or any({source, target} <= group for group in groups)
            assert _same_language_family(source_path, target_path) == expected, (source, target)


def test_M4_class_type_parameter(project_factory):
    p = project_factory(
        {"app.ts": "class Foo { m() {} }\nclass C<Foo> { bad(x: Foo) { x.m() } }\nfunction good(x: Foo) { x.m() }"}
    )
    c = calls(p)
    assert ("app.ts", "good", "app.ts", "Foo.m") in c, c
    assert ("app.ts", "C.bad", "app.ts", "Foo.m") not in c, c


def test_M5_named_function_expression(project_factory):
    p = project_factory(
        {
            "app.ts": "export function bad() { const x = function helper() { helper() }; x() }\nfunction good() { helper() }",
            "dep.ts": "export function helper() {}",
        }
    )
    c = calls(p)
    assert ("app.ts", "good", "dep.ts", "helper") in c, c
    assert not [e for e in c if e[0] == "app.ts" and e[1] != "good" and e[3] == "helper"], c


def test_M6_type_value_namespaces(project_factory):
    p = project_factory(
        {
            "api.ts": "export const Config = { load() {} }; export interface Config { read(): void }",
            "app.ts": "import { Config } from './api';\nfunction goodValue() { Config.load() }\nfunction goodType(x: Config) { x.read() }\nfunction bad(x: Config) { x.load() }",
        }
    )
    c = calls(p)
    assert ("app.ts", "goodValue", "api.ts", "Config.load") in c, c
    assert ("app.ts", "goodType", "api.ts", "Config.read") in c, c
    assert ("app.ts", "bad", "api.ts", "Config.load") not in c, c
