"""W101+W102+W106 — tests for the most recent probe additions.

W101: cross-file refactor probe (move X from A to B)
W102: API surface probe (top-level def/class scan)
W106: enriched reachability probe with verdict_directive
"""

from __future__ import annotations

from roam.plan.compiler import (
    _API_SURFACE_RE,
    _REACHABILITY_RE,
    _REFACTOR_MOVE_RE,
    _probe_api_surface_for_task,
    _probe_reachability_for_task,
    _probe_refactor_move_for_task,
)

# ---- W101 refactor move ----


def test_w101_refactor_move_regex_matches():
    for s in [
        "move foo from a.py to b.py",
        "extract `bar` from src/x.py into src/y.py",
        "relocate baz to src/z.py",
        "hoist quux to lib/util.py",
    ]:
        assert _REFACTOR_MOVE_RE.search(s), s


def test_w101_refactor_move_regex_misses_unrelated():
    for s in [
        "what is foo",
        "remove foo from src/x.py",  # not a move
        "delete src/x.py",
    ]:
        assert not _REFACTOR_MOVE_RE.search(s), s


def test_w101_refactor_move_returns_none_when_no_match():
    out = _probe_refactor_move_for_task("what is foo", cwd=None)
    assert out is None


def test_w101_refactor_move_extracts_symbol_and_paths(monkeypatch):
    from roam.plan import compiler as M

    monkeypatch.setattr(
        M,
        "_run_roam",
        lambda *a, **k: {
            "consumers": {
                "call": [{"location": "x.py:1", "name": "caller1", "kind": "function", "scope": "production"}],
                "import": [],
            }
        },
    )
    out = _probe_refactor_move_for_task("move foo from a.py to b.py", cwd=None)
    assert out is not None
    rm = out["refactor_move"]
    assert rm["symbol"] == "foo"
    assert rm["source_file"] == "a.py"
    assert rm["destination_file"] == "b.py"
    assert rm["callers_count"] == 1


# ---- W102 API surface ----


def test_w102_api_surface_regex_matches():
    for s in [
        "what's exported by src/foo.py",
        "what are the public functions of src/foo.py",
        "what does this module expose",
        "what's the API",
    ]:
        assert _API_SURFACE_RE.search(s), s


def test_w102_api_surface_no_named_paths_returns_none():
    out = _probe_api_surface_for_task("what's exported", named_paths=[], cwd=None)
    assert out is None


def test_w102_api_surface_extracts_top_level_def_class(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "def public_fn(): pass\n"
        "class PublicClass: pass\n"
        "async def async_fn(): pass\n"
        "def _private(): pass\n"  # underscore — excluded
        "    def indented(): pass\n"  # indented — not top-level
    )
    out = _probe_api_surface_for_task(
        "what's exported by x.py",
        named_paths=["x.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    exports = out["api_surface"]["exports"]
    names = {e["name"] for e in exports}
    assert names == {"public_fn", "PublicClass", "async_fn"}
    assert "_private" not in names
    assert "indented" not in names


# ---- W106 reachability enriched ----


def test_w106_reachability_regex_matches():
    for s in [
        "is `foo` reachable from `bar`",
        "does `foo` depend on `bar`",
        "can `foo` call `bar`",
    ]:
        assert _REACHABILITY_RE.search(s), s


def test_w106_reachability_needs_two_backticked_symbols():
    out = _probe_reachability_for_task("is `foo` reachable from x", cwd=None)
    assert out is None
    out = _probe_reachability_for_task("is foo reachable from bar", cwd=None)
    assert out is None


def test_w106_reachability_emits_verdict_directive(monkeypatch):
    from roam.plan import compiler as M

    monkeypatch.setattr(
        M,
        "_run_roam",
        lambda args, *a, **k: (
            {"affected_file_list": ["src/path_to_bar.py"], "affected_files_total": 1} if args[0] == "impact" else {}
        ),
    )
    out = _probe_reachability_for_task(
        "is `foo` reachable from `bar`",
        cwd=None,
    )
    assert out is not None
    r = out["reachability"]
    assert "verdict_directive" in r
    assert r["reachable"] is True
    assert "TRUST" not in r["verdict_directive"]  # only present in non-reachable
    assert "REACHABLE" in r["verdict_directive"]


def test_w106_reachability_non_reachable_includes_trust_directive(monkeypatch):
    from roam.plan import compiler as M

    monkeypatch.setattr(
        M,
        "_run_roam",
        lambda args, *a, **k: (
            {"affected_file_list": ["unrelated.py"], "affected_files_total": 1} if args[0] == "impact" else {}
        ),
    )
    out = _probe_reachability_for_task(
        "is `foo` reachable from `bar`",
        cwd=None,
    )
    assert out is not None
    vd = out["reachability"]["verdict_directive"]
    # Non-reachable case must explicitly tell the agent NOT to re-verify
    assert "do NOT" in vd or "Trust" in vd
    assert "NOT REACHABLE" in vd


def test_w106_reachability_handles_int_affected_symbols(monkeypatch):
    """W106 fix: roam impact returns affected_symbols as int (count) when
    not in --detail mode. Probe must not crash on that shape."""
    from roam.plan import compiler as M

    monkeypatch.setattr(
        M,
        "_run_roam",
        lambda args, *a, **k: (
            {"affected_file_list": [], "affected_files_total": 0, "affected_symbols": 5}  # INT, not list
            if args[0] == "impact"
            else {}
        ),
    )
    # Should not raise TypeError
    out = _probe_reachability_for_task(
        "is `foo` reachable from `bar`",
        cwd=None,
    )
    assert out is not None
    assert out["reachability"]["affected_total"] in (5, 0)


# ---- _embed_move_caller_imports dedup (repeated callers from one file) ----


def test_embed_move_caller_imports_scans_each_file_once(tmp_path, monkeypatch):
    """Repeated callers from the SAME file must trigger exactly one
    filesystem scan, not one per caller row. Pins the dedup so a future
    refactor cannot reintroduce per-row exists/stat/read_text reads."""
    from roam.plan import compiler as M

    caller = tmp_path / "uses_widget.py"
    caller.write_text("from lib.widget import Widget\n\n\ndef go():\n    return Widget()\n")

    # Five caller rows, all pointing at the same file (distinct lines).
    callers = [{"location": f"uses_widget.py:{ln}"} for ln in (1, 5, 12, 30, 44)]

    reads: list[str] = []
    real_read_text = M.Path.read_text

    def counting_read_text(self, *a, **k):
        reads.append(str(self))
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(M.Path, "read_text", counting_read_text)

    out = M._embed_move_caller_imports(callers, "Widget", str(tmp_path))

    assert out == {"uses_widget.py": "from lib.widget import Widget"}
    # The file is read exactly once despite five caller rows.
    assert reads.count(str(caller)) == 1


def test_embed_move_caller_imports_caps_at_eight_distinct_files(tmp_path):
    """The 8-file cap counts DISTINCT files, so duplicate rows from the
    first file do not consume cap slots that later distinct files need."""
    from roam.plan import compiler as M

    # First file appears 4 times; then 9 more distinct files. With per-row
    # capping the later distinct files would be starved; with path-dedup the
    # cap admits 8 distinct files.
    callers = [{"location": "f0.py:1"}] * 4
    for i in range(9):
        name = f"f{i}.py"
        (tmp_path / name).write_text(f"from pkg import Sym  # {name}\n")
        callers.append({"location": f"{name}:1"})

    out = M._embed_move_caller_imports(callers, "Sym", str(tmp_path))

    # 8 DISTINCT files admitted (f0..f7); the duplicate f0.py rows did not
    # eat slots, so exactly the cap's worth of distinct files are scanned.
    assert len(out) == 8
    assert "f8.py" not in out
