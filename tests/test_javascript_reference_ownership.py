"""JS/TS edges require a supported receiver or a non-local binding (issue 136)."""

import sqlite3

import pytest

from tests.conftest import index_in_process

REPRO = {
    "server/knob.ts": "export interface Knob {\n readonly min: number\n readonly max: number\n}\n",
    "client/clamp.ts": "export function clamp(v: number, lo: number, hi: number): number {\n return Math.max(lo, Math.min(hi, v))\n}\n",
    "server/plate.ts": "export interface Plate {\n readonly value: number\n}\n",
    "client/show.ts": "export function show(value: number): string {\n return String(value)\n}\n",
}


def edges(project):
    with sqlite3.connect(project / ".roam/index.db") as conn:
        return conn.execute(
            "SELECT sf.path, s.name, e.line, e.kind, t.name, tf.path "
            "FROM edges e JOIN symbols s ON s.id=e.source_id "
            "JOIN symbols t ON t.id=e.target_id JOIN files sf ON sf.id=s.file_id "
            "JOIN files tf ON tf.id=t.file_id ORDER BY sf.path,e.line,t.name"
        ).fetchall()


@pytest.mark.parametrize("target", ["max", "min", "value"])
def test_issue_136_no_phantom_edge(project_factory, target):
    project = project_factory(REPRO)
    assert not [e for e in edges(project) if e[4] == target], edges(project)


CONTROLS = [
    (
        {
            "dep.ts": "export function parse() {}\n",
            "app.ts": "import { parse } from './dep'\nexport function run() { parse() }\n",
        },
        "run",
        "parse",
        "dep.ts",
    ),
    ({"app.ts": "class Worker {\n work() {}\n run() { this.work() }\n}\n"}, "run", "work", "app.ts"),
    (
        {
            "app.ts": "class Worker { work() {} }\nexport function run() { const worker = new Worker(); worker.work() }\n"
        },
        "run",
        "work",
        "app.ts",
    ),
    (
        {
            "dep.ts": "export function parse() {}\n",
            "app.ts": "import * as util from './dep'\nexport function run() { util.parse() }\n",
        },
        "run",
        "parse",
        "dep.ts",
    ),
    (
        {
            "api.ts": "export const api = {\n get() { return 1 }\n}\n",
            "app.ts": "import { api } from './api'\nexport function run() { api.get() }\n",
        },
        "run",
        "get",
        "api.ts",
    ),
    ({"app.ts": "function parse() {}\nexport function run() { parse() }\n"}, "run", "parse", "app.ts"),
    (
        {
            "dep.ts": "export function parse() {}\n",
            "barrel.ts": "export { parse } from './dep'\n",
            "app.ts": "import { parse } from './barrel'\nexport function run() { parse() }\n",
        },
        "run",
        "parse",
        "dep.ts",
    ),
    (
        {
            "Widget.tsx": "export function Widget() { return <div/> }\n",
            "app.tsx": "import { Widget } from './Widget'\nexport function App() { return <Widget/> }\n",
        },
        "App",
        "Widget",
        "Widget.tsx",
    ),
]


@pytest.mark.parametrize(
    "files,caller,target,target_file",
    CONTROLS,
    ids=["imported-bare", "this", "instance", "namespace", "imported-object", "same-file", "reexport", "react"],
)
def test_supported_control(project_factory, files, caller, target, target_file):
    project = project_factory(files)
    assert any(e[1] == caller and e[3] == "call" and e[4:] == (target, target_file) for e in edges(project)), edges(
        project
    )

    caller_file = "app.tsx" if "app.tsx" in files else "app.ts"
    (project / caller_file).write_text(files[caller_file] + "// changed caller\n")
    output, code = index_in_process(project)
    assert code == 0, output
    assert any(e[1] == caller and e[3] == "call" and e[4:] == (target, target_file) for e in edges(project)), edges(
        project
    )


def test_incremental_keeps_controls_and_removes_phantoms(project_factory):
    files = dict(REPRO)
    files["client/show.ts"] = (
        "import { parse } from '../dep'\nexport function show(value: number) { parse(); return String(value) }\n"
    )
    files["dep.ts"] = "export function parse() {}\n"
    project = project_factory(files)
    before = edges(project)
    (project / "client/show.ts").write_text(files["client/show.ts"].replace("parse();", "parse(); Math.max(1, 2);"))
    output, code = index_in_process(project)
    assert code == 0, output
    after = edges(project)
    assert not [e for e in after if e[4] in {"min", "max", "value"}], after
    assert any(e[1] == "show" and e[4:] == ("parse", "dep.ts") for e in after)
    assert [e for e in before if e[4] == "parse"] == [e for e in after if e[4] == "parse"]


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize(
    "expression", ["Math.max(1, 2)", "logger.max()", "unknown.max()", "make().max()", "new unknown.max()"]
)
def test_member_does_not_bind_by_property_name(project_factory, extension, expression):
    project = project_factory(
        {
            f"app.{extension}": f"function max() {{}}\nexport function run() {{ {expression} }}\n",
            f"other.{extension}": "export class Other { max() {} }\n",
        }
    )
    assert not [e for e in edges(project) if e[3] == "call" and e[4] == "max"], edges(project)


@pytest.mark.parametrize("binding", ["value", "{ value }", "[value]", "value = 1", "...value"])
def test_parameter_bindings_are_not_global_references(project_factory, binding):
    project = project_factory(
        {
            "app.js": f"export function run({binding}) {{ consume(value); return {{ value }} }}\n",
            "other.js": "export const value = 1\n",
        }
    )
    assert not [e for e in edges(project) if e[4] == "value"], edges(project)


@pytest.mark.parametrize(
    "declaration", ["const value = 1", "let { x: value } = input", "let [value] = input", "var value = 1"]
)
def test_local_declarations_are_not_global_references(project_factory, declaration):
    project = project_factory(
        {
            "app.js": f"export function run() {{ {declaration}; consume(value) }}\n",
            "other.js": "export const value = 1\n",
        }
    )
    assert not [e for e in edges(project) if e[4] == "value"], edges(project)


def test_shadowed_import_receiver_does_not_bind(project_factory):
    project = project_factory(
        {
            "app.ts": "import * as util from './dep'\nexport function run(util: unknown) { util.parse() }\n",
            "dep.ts": "export function parse() {}\n",
        }
    )
    assert not [e for e in edges(project) if e[3] == "call" and e[4] == "parse"], edges(project)


def test_bare_callback_kept_but_parameter_callback_not_guessed(project_factory):
    project = project_factory(
        {
            "app.ts": "import { callback } from './dep'\nexport function run() { consume(callback) }\nexport function shadow(callback: unknown) { consume(callback) }\n",
            "dep.ts": "export function callback() {}\n",
        }
    )
    refs = [e for e in edges(project) if e[3] == "reference" and e[4] == "callback"]
    assert [e[1] for e in refs] == ["run"], refs


@pytest.mark.parametrize("source_ext,target_ext", [("ts", "py"), ("py", "ts")])
def test_name_fallback_stays_in_language_family(project_factory, source_ext, target_ext):
    caller = "export function run() { foreign() }\n" if source_ext == "ts" else "def run():\n    foreign()\n"
    target = "export function foreign() {}\n" if target_ext == "ts" else "def foreign():\n    pass\n"
    project = project_factory({f"app.{source_ext}": caller, f"other.{target_ext}": target})
    assert not [e for e in edges(project) if e[4] == "foreign"], edges(project)


@pytest.mark.parametrize("extension", ["py", "ts"])
def test_same_language_fallback_still_works(project_factory, extension):
    caller = "export function run() { helper() }\n" if extension == "ts" else "def run():\n    helper()\n"
    target = "export function helper() {}\n" if extension == "ts" else "def helper():\n    pass\n"
    project = project_factory({f"client/app.{extension}": caller, f"lib/other.{extension}": target})
    assert any(e[4] == "helper" and e[3] == "call" for e in edges(project)), edges(project)


def test_receiver_cache_separates_unknown_and_imported_namespace(project_factory):
    project = project_factory(
        {
            "app.ts": "import * as util from './dep'\nexport function unknownRun(unknown: any) { unknown.parse() }\nexport function run() { util.parse() }\n",
            "dep.ts": "export function parse() {}\n",
        }
    )
    assert len([e for e in edges(project) if e[4] == "parse" and e[3] == "call"]) == 1
    # Contract strengthening: keep count=1; separate callers so a false
    # unknown edge cannot deduplicate into the supported run edge.
    from tests.test_js_edge_resolution import _calls

    assert _calls(project) == {("app.ts", "run", "dep.ts", "parse")}


def test_var_binding_is_hoisted_out_of_block(project_factory):
    project = project_factory(
        {
            "app.js": "export function run() { if (true) { var value = 1 } consume(value) }\n",
            "other.js": "export const value = 2\n",
        }
    )
    assert not [e for e in edges(project) if e[4] == "value"], edges(project)


def test_local_constructor_parameter_shadows_import(project_factory):
    project = project_factory(
        {
            "app.ts": "import { Worker } from './dep'\nexport function run(Worker: any) { const x = new Worker(); x.work() }\n",
            "dep.ts": "export class Worker { work() {} }\n",
        }
    )
    assert not [e for e in edges(project) if e[3] == "call" and e[4] in {"work", "Worker"}], edges(project)


@pytest.mark.parametrize(
    "declaration", ["for (const value of []) { consume(value) }", "try {} catch (value) { consume(value) }"]
)
def test_loop_and_catch_bindings_are_local(project_factory, declaration):
    project = project_factory(
        {
            "app.js": f"export function run() {{ {declaration} }}\n",
            "other.js": "export const value = 2\n",
        }
    )
    assert not [e for e in edges(project) if e[4] == "value"], edges(project)


def test_namespace_cannot_call_private_function(project_factory):
    project = project_factory(
        {
            "app.ts": "import * as util from './dep'\nexport function run() { util.hidden() }\n",
            "dep.ts": "function hidden() {}\nexport function visible() {}\n",
        }
    )
    assert not [e for e in edges(project) if e[4] == "hidden"], edges(project)


def test_incremental_changed_file_drops_pre_repair_persisted_edge(project_factory):
    project = project_factory(REPRO)
    # Reproduce the persisted pre-repair value edge, then change its source file.
    with sqlite3.connect(project / ".roam/index.db") as conn:
        source_id, source_file_id = conn.execute(
            "SELECT s.id,s.file_id FROM symbols s JOIN files f ON f.id=s.file_id "
            "WHERE s.name='show' AND f.path='client/show.ts'"
        ).fetchone()
        target_id = conn.execute("SELECT id FROM symbols WHERE qualified_name='Plate.value'").fetchone()[0]
        conn.execute(
            "INSERT INTO edges (source_id,target_id,kind,line,source_file_id) VALUES (?,?,'reference',2,?)",
            (source_id, target_id, source_file_id),
        )
    assert any(e[4] == "value" for e in edges(project))
    (project / "client/show.ts").write_text(REPRO["client/show.ts"] + "// changed source\n")
    output, code = index_in_process(project)
    assert code == 0, output
    assert not [e for e in edges(project) if e[4] == "value"], edges(project)


@pytest.mark.parametrize("annotation", ["Api", "Api<string>"])
def test_explicit_parameter_type_keeps_only_its_own_member(project_factory, annotation):
    project = project_factory(
        {
            "app.ts": f"import {{ Api }} from './api'\nexport function run(api: {annotation}) {{ api.get() }}\n",
            "api.ts": "export interface Api<T = string> { get(): T }\n",
            "decoy.ts": "export interface Other { get(): string }\n",
        }
    )
    assert [e[5] for e in edges(project) if e[3] == "call" and e[4] == "get"] == ["api.ts"]


def test_annotated_variable_keeps_member_and_unknown_variable_drops_it(project_factory):
    project = project_factory(
        {
            "app.ts": "interface Api { get(): string }\nexport function run() { const api: Api = factory(); api.get() }\nexport function unknownRun() { const unknown = factory(); unknown.get() }\n",
        }
    )
    assert len([e for e in edges(project) if e[3] == "call" and e[4] == "get"]) == 1
    # Unchanged count, now with caller identities that expose dedup masking.
    from tests.test_js_edge_resolution import _calls

    assert _calls(project) == {("app.ts", "run", "app.ts", "Api.get")}
