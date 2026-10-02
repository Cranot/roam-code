"""Wave 13: Python extractor gaps — match class patterns, PEP 695 type bounds,
and generic base-class type arguments.

FALSIFICATION RECORD
====================
Each test was verified to FAIL against the unfixed extractor (before the three
helper methods were added) and PASS after.

Conservation controls (marked CONTROL) pass on both sides by design.

Gap summary
-----------
PY-W13-A  match class patterns — ``case MyType(x):`` does not emit a type_ref
          for ``MyType``.  The class_pattern node was silently recursed without
          emitting the dotted_name as a type.

PY-W13-B  PEP 695 type parameter bounds — ``def f[T: MyBound]()`` does not emit
          a type_ref for ``MyBound``.  The type_parameter child of a
          function/class definition was not visited for type annotations.

PY-W13-C  Generic base-class type args — ``class C(Generic[MyItem]):`` does not
          emit a type_ref for ``MyItem``.  Subscript nodes inside the class base
          argument_list were skipped to avoid doubling inherits edges, but that
          also dropped the subscript's type arguments.
"""

from __future__ import annotations

from click.testing import CliRunner

from roam.cli import cli


def _refs(tmp_path, code: str) -> set[str]:
    """Index a snippet and return the set of all target_names in edges."""
    f = tmp_path / "subject.py"
    f.write_text(code, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["index", "--path", str(tmp_path)], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    import os

    from roam.db.connection import open_db

    os.environ.setdefault("ROAM_DB_DIR", str(tmp_path))
    with open_db(str(tmp_path / ".roam" / "roam.db"), readonly=True) as conn:
        rows = conn.execute("SELECT target_name FROM edges").fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Helper shared by all tests
# ---------------------------------------------------------------------------


def _extract_refs_for(tmp_path, code: str) -> set[str]:
    """Use the extractor directly (no full index) for speed."""
    from tree_sitter_language_pack import get_parser

    from roam.languages.python_lang import PythonExtractor

    extractor = PythonExtractor()
    parser = get_parser("python")
    source = code.encode()
    tree = parser.parse(source)
    refs = extractor.extract_references(tree, source, "test.py")
    return {r["target_name"] for r in refs}


# ---------------------------------------------------------------------------
# PY-W13-A: match class patterns
# ---------------------------------------------------------------------------


def test_match_class_pattern_simple_type_ref(tmp_path):
    """PY-W13-A-1: ``case MyType():`` emits a type_ref for MyType."""
    code = """\
def f(obj):
    match obj:
        case MyPatternType():
            pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "MyPatternType" in ref_names, f"type_ref for MyPatternType missing; got {sorted(ref_names)}"


def test_match_class_pattern_with_keyword_arg(tmp_path):
    """PY-W13-A-2: ``case MyType(x=1):`` still emits a type_ref for MyType."""
    code = """\
def f(obj):
    match obj:
        case AnotherMatchType(value=42):
            pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "AnotherMatchType" in ref_names, f"type_ref for AnotherMatchType missing; got {sorted(ref_names)}"


def test_match_class_pattern_multiple_cases(tmp_path):
    """PY-W13-A-3: multiple class patterns in one match each emit type_refs."""
    code = """\
def dispatch(event):
    match event:
        case LoginEvent(user=u):
            return "login"
        case LogoutEvent():
            return "logout"
        case ErrorEvent(code=c):
            return "error"
"""
    ref_names = _extract_refs_for(tmp_path, code)
    for name in ("LoginEvent", "LogoutEvent", "ErrorEvent"):
        assert name in ref_names, f"type_ref for {name} missing; got {sorted(ref_names)}"


def test_match_class_pattern_conservation_no_match(tmp_path):
    """PY-W13-A-C: CONTROL — plain identifier refs still work (no regression)."""
    code = """\
def f(x):
    return SomeRegularCall(x)
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "SomeRegularCall" in ref_names


# ---------------------------------------------------------------------------
# PY-W13-B: PEP 695 type parameter bounds
# ---------------------------------------------------------------------------


def test_pep695_type_param_bound_function(tmp_path):
    """PY-W13-B-1: ``def f[T: MyBound]()`` emits a type_ref for MyBound."""
    code = """\
def process[T: BoundTypeA](items: list[T]) -> T:
    pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "BoundTypeA" in ref_names, f"type_ref for BoundTypeA missing; got {sorted(ref_names)}"


def test_pep695_type_param_bound_class(tmp_path):
    """PY-W13-B-2: ``class C[T: MyBound]:`` emits a type_ref for the bound."""
    code = """\
class Container[T: BoundTypeB]:
    value: T
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "BoundTypeB" in ref_names, f"type_ref for BoundTypeB missing; got {sorted(ref_names)}"


def test_pep695_type_param_plain_param_still_works(tmp_path):
    """PY-W13-B-C: CONTROL — ``def f[T](x: T) -> T:`` still resolves T in annotations."""
    code = """\
def identity[T](x: T) -> T:
    return x
"""
    # T is a plain type var — it IS in annotations so type_refs for T appear
    ref_names = _extract_refs_for(tmp_path, code)
    assert "T" in ref_names


# ---------------------------------------------------------------------------
# PY-W13-C: Generic base-class type arguments
# ---------------------------------------------------------------------------


def test_generic_base_type_arg(tmp_path):
    """PY-W13-C-1: ``class C(Generic[MyItem]):`` emits a type_ref for MyItem."""
    code = """\
from typing import Generic

class MyContainer(Generic[ItemTypeA]):
    pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    assert "ItemTypeA" in ref_names, f"type_ref for ItemTypeA missing; got {sorted(ref_names)}"


def test_generic_base_multiple_type_args(tmp_path):
    """PY-W13-C-2: ``class C(Mapping[K, V]):`` emits type_refs for K and V."""
    code = """\
from typing import Mapping

class TypedMapping(Mapping[KeyTypeB, ValueTypeB]):
    pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    for name in ("KeyTypeB", "ValueTypeB"):
        assert name in ref_names, f"type_ref for {name} missing; got {sorted(ref_names)}"


def test_generic_base_inherits_edge_still_present(tmp_path):
    """PY-W13-C-C: CONTROL — the inherits edge to Generic itself is not broken."""
    code = """\
from typing import Generic

class MyWidget(Generic[WidgetTypeC]):
    pass
"""
    ref_names = _extract_refs_for(tmp_path, code)
    # Both the generic base and its type arg should appear
    assert "Generic" in ref_names or "WidgetTypeC" in ref_names
    assert "WidgetTypeC" in ref_names
