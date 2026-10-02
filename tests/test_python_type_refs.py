"""Python type-ref gap tests — Wave 1 (items #22a–e).

Covers:
  PY-A  cast(Config, val)           → type_ref to Config
  PY-B1 isinstance(x, Handler)      → type_ref to Handler
  PY-B2 isinstance(x, (A, B))       → type_refs to A and B
  PY-B3 isinstance(x, str)          → no type_ref (builtin)
  PY-C  @overload decorator         → call ref (already handled — non-regression)
  PY-D  class Point(NamedTuple)     → type_ref to NamedTuple
  PY-E  __all__ = ["Foo", "Bar"]    → export refs to Foo and Bar
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "foo.py"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language(file_path)
    assert language is not None
    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _type_ref_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "type_ref"}


def _export_ref_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "export"}


def _all_ref_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs}


# ---------------------------------------------------------------------------
# PY-A  cast() first argument is a type_ref
# ---------------------------------------------------------------------------


def test_py_cast_emits_type_ref():
    """cast(Config, raw) must produce a type_ref edge to Config."""
    source = """\
from typing import cast
x = cast(Config, raw)
"""
    _, refs = _parse(source)
    assert "Config" in _type_ref_targets(refs), f"Config missing from type_refs; got {_type_ref_targets(refs)}"


def test_py_typing_cast_attribute_emits_type_ref():
    """typing.cast(Config, raw) must produce a type_ref edge to Config."""
    source = """\
import typing
x = typing.cast(Config, raw)
"""
    _, refs = _parse(source)
    assert "Config" in _type_ref_targets(refs), f"Config missing from type_refs; got {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# PY-B  isinstance / issubclass type arguments are type_refs
# ---------------------------------------------------------------------------


def test_py_isinstance_single_type_ref():
    """isinstance(x, Handler) must produce a type_ref to Handler."""
    source = """\
if isinstance(x, Handler):
    pass
"""
    _, refs = _parse(source)
    assert "Handler" in _type_ref_targets(refs), f"Handler missing from type_refs; got {_type_ref_targets(refs)}"


def test_py_isinstance_tuple_type_refs():
    """isinstance(x, (A, B)) must produce type_refs to both A and B."""
    source = """\
if isinstance(x, (Alpha, Beta)):
    pass
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Alpha" in targets, f"Alpha missing from type_refs; got {targets}"
    assert "Beta" in targets, f"Beta missing from type_refs; got {targets}"


def test_py_isinstance_builtin_not_emitted():
    """isinstance(x, str) must NOT produce a type_ref (str is builtin)."""
    source = """\
if isinstance(x, str):
    pass
"""
    _, refs = _parse(source)
    assert "str" not in _type_ref_targets(refs), "str is builtin and must not appear as type_ref"


def test_py_issubclass_type_ref():
    """issubclass(cls, Base) must produce a type_ref to Base."""
    source = """\
if issubclass(cls, Base):
    pass
"""
    _, refs = _parse(source)
    assert "Base" in _type_ref_targets(refs), f"Base missing from type_refs; got {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# PY-C  decorators (non-regression — already handled)
# ---------------------------------------------------------------------------


def test_py_decorator_emits_call_ref():
    """@overload decorator must produce a call ref (non-regression)."""
    source = """\
from typing import overload

@overload
def process(x: int) -> int: ...
"""
    _, refs = _parse(source)
    call_targets = {r["target_name"] for r in refs if r.get("kind") == "call"}
    assert "overload" in call_targets, f"overload call ref missing; got {call_targets}"


# ---------------------------------------------------------------------------
# PY-D  NamedTuple base → type_ref
# ---------------------------------------------------------------------------


def test_py_namedtuple_base_type_ref():
    """class Point(NamedTuple) must produce a type_ref to NamedTuple."""
    source = """\
from typing import NamedTuple

class Point(NamedTuple):
    x: float
    y: float
"""
    _, refs = _parse(source)
    assert "NamedTuple" in _type_ref_targets(refs), f"NamedTuple missing from type_refs; got {_type_ref_targets(refs)}"


def test_py_namedtuple_kind():
    """class Point(NamedTuple) must have symbol kind 'namedtuple'."""
    source = """\
from typing import NamedTuple

class Point(NamedTuple):
    x: float
"""
    syms, _ = _parse(source)
    point_sym = next((s for s in syms if s["name"] == "Point"), None)
    assert point_sym is not None, "Point symbol not found"
    assert point_sym["kind"] == "namedtuple", f"Expected kind 'namedtuple', got {point_sym['kind']!r}"


# ---------------------------------------------------------------------------
# PY-E  __all__ = [...] → export refs
# ---------------------------------------------------------------------------


def test_py_dunder_all_export_refs():
    """__all__ = ['Config', 'Handler'] must produce export refs."""
    source = """\
__all__ = ["Config", "Handler"]
"""
    _, refs = _parse(source)
    export_targets = _export_ref_targets(refs)
    assert "Config" in export_targets, f"Config missing from export refs; got {export_targets}"
    assert "Handler" in export_targets, f"Handler missing from export refs; got {export_targets}"


def test_py_dunder_all_augmented_export_refs():
    """__all__ += ['Extra'] must also produce an export ref."""
    source = """\
__all__ = ["Base"]
__all__ += ["Extra"]
"""
    _, refs = _parse(source)
    export_targets = _export_ref_targets(refs)
    assert "Base" in export_targets, f"Base missing from export refs; got {export_targets}"
    assert "Extra" in export_targets, f"Extra missing from export refs; got {export_targets}"


# ---------------------------------------------------------------------------
# Non-regression: existing call + import refs unaffected
# ---------------------------------------------------------------------------


def test_py_call_refs_unaffected():
    """Adding new type-ref extraction must not break call refs."""
    source = """\
result = process(data)
obj.method(value)
"""
    _, refs = _parse(source)
    call_targets = {r["target_name"] for r in refs if r.get("kind") == "call"}
    assert "process" in call_targets, f"process call missing; got {call_targets}"
