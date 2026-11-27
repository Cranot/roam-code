"""Rust method calls resolve only through a receiver whose type the code shows (#136).

`resp.status()` was resolved by the method name alone, so it could bind to any
`status` in the repository: in the issue's repro, an unrelated route handler in
another crate directory.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import git_init, index_in_process

LIB = """\
pub struct Response;
impl Response {
    pub fn status(&self) -> u16 { 200 }
    pub fn new() -> Response { Response }
    pub fn twice(&self) -> u16 { self.status() + Self::new().status() }
}
pub trait Named {
    fn name(&self) -> String;
    fn hello(&self) -> String { self.name() }
}
impl Named for Response {
    fn name(&self) -> String { String::new() }
}
pub fn status() -> &'static str { "ok" }
pub fn len() -> usize { 0 }
pub fn by_param(resp: &Response) -> u16 { resp.status() }
pub fn by_mut_param(resp: &mut Response) -> u16 { resp.status() }
pub fn by_constructor() -> u16 { let r = Response::new(); r.status() }
pub fn by_annotation() -> u16 { let r: Response = Response; r.status() }
pub fn by_literal() -> u16 { let r = Response {}; r.status() }
pub fn by_trait_impl(resp: &Response) -> String { resp.name() }
pub fn unknown_receiver(v: Vec<u8>) -> usize { v.len() }
pub fn chained() -> u16 { make().status() }
pub fn make() -> Response { Response }
pub fn by_shadow(v: Response) -> u16 { let v = v.status(); v }
pub fn by_closure_shadow(r: Response) -> u16 { let f = |r: u8| r.status(); f(0) }
pub struct Wrap { inner: Response }
impl Wrap { pub fn by_self_field(&self) -> u16 { self.inner.status() } }
pub fn by_field(w: &Wrap) -> u16 { w.inner.status() }
pub struct Builder { n: u8 }
impl Builder {
    pub fn new() -> Self { Builder { n: 0 } }
    pub fn n(&mut self, n: u8) -> &mut Builder { self.n = n; self }
    pub fn build(&self) -> Response { Response }
}
pub fn by_chain() -> u16 { Builder::new().n(1).build().status() }
pub fn by_let_chain() -> u16 { let r = Builder::new().build(); r.status() }
pub fn by_plain_call() -> u16 { build() }
pub struct Routes { get: u8 }
pub fn by_plain_field_name() -> u16 { get(1) }
pub struct Field { inner: other::Field }
impl Field {
    pub fn name(&self) -> String { String::new() }
    pub fn label(&self) -> String { self.inner.name() }
}
impl std::str::FromStr for Response {
    type Err = u8;
    fn from_str(_: &str) -> Result<Self, u8> { Ok(Response) }
}
pub fn by_plain_err() -> Result<u8, u8> { Err(1) }
"""

# The four files of the issue's Rust repro, verbatim.
REPRO = {
    "app/src/lib.rs": "use std::sync::mpsc;\npub fn notify(tx: &mpsc::Sender<u32>) {\n    tx.send(1).unwrap();\n}\n",
    "app/src/http.rs": "pub fn check(resp: &Response) -> u16 {\n    resp.status()\n}\npub struct Response;\n",
    "api/src/routes.rs": 'pub async fn status() -> &\'static str { "ok" }\n',
    "web/client.ts": (
        "export function unwrap<T>(v: T | undefined): T {\n"
        '  if (v === undefined) throw new Error("missing");\n'
        "  return v;\n}\n"
    ),
}


def _call_edges(root, files):
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8", newline="\n")
    git_init(root)
    output, code = index_in_process(root, "--force")
    assert code == 0, output
    conn = sqlite3.connect(root / ".roam" / "index.db")
    rows = conn.execute(
        """SELECT a.qualified_name, fa.path, b.qualified_name, fb.path
           FROM edges e
           JOIN symbols a ON a.id = e.source_id JOIN files fa ON fa.id = a.file_id
           JOIN symbols b ON b.id = e.target_id JOIN files fb ON fb.id = b.file_id
           WHERE e.kind = 'call'"""
    ).fetchall()
    conn.close()
    return rows


@pytest.fixture(scope="module")
def pairs(tmp_path_factory):
    rows = _call_edges(tmp_path_factory.mktemp("rust-member-calls"), {"core/src/lib.rs": LIB})
    return {(src, dst) for src, _, dst, _ in rows}


def test_the_issue_repro_binds_no_method_call_by_name_alone(tmp_path):
    assert _call_edges(tmp_path, REPRO) == []


@pytest.mark.parametrize(
    ("caller", "callee"),
    [
        ("by_param", "Response::status"),
        ("by_mut_param", "Response::status"),
        ("by_constructor", "Response::status"),
        ("by_annotation", "Response::status"),
        ("by_literal", "Response::status"),
        ("Response::twice", "Response::status"),
        ("Named::hello", "Named::name"),
        ("by_trait_impl", "Response::Named::name"),
        # A call inside the receiver is a reference of its own (it was dropped).
        ("chained", "make"),
        # The binding in effect at the call: the parameter, not the `let` it feeds.
        ("by_shadow", "Response::status"),
        # A field path on a typed value: the field's declared type.
        ("Wrap::by_self_field", "Response::status"),
        ("by_field", "Response::status"),
        # A chain keeps its owner through the return types this file declares.
        ("by_chain", "Builder::n"),
        ("by_chain", "Builder::build"),
        ("by_chain", "Response::status"),
        ("by_let_chain", "Response::status"),
    ],
)
def test_a_method_call_binds_to_the_method_of_the_shown_type(pairs, caller, callee):
    assert (caller, callee) in pairs, sorted(p for p in pairs if p[0] == caller)
    assert (caller, "status") not in pairs


@pytest.mark.parametrize(
    ("caller", "wrong"),
    [
        ("unknown_receiver", "len"),
        ("chained", "status"),
        # The closure parameter `r: u8` shadows the outer `r: Response`.
        ("by_closure_shadow", "Response::status"),
        # A plain call `f()` is never a method call (`build` exists only as a method).
        ("by_plain_call", "Builder::build"),
        # Nor a struct field (axum's `get(handler)` bound to `MethodRouter::get`).
        ("by_plain_field_name", "Routes::get"),
        # `inner: other::Field` inside `Field` is another type of the same name.
        ("Field::label", "Field::name"),
        # Nor an associated type (`Err(1)` bound to `type Err`).
        ("by_plain_err", "Response::std::str::FromStr::Err"),
    ],
)
def test_a_receiver_of_unshown_type_makes_no_edge(pairs, caller, wrong):
    assert (caller, wrong) not in pairs
