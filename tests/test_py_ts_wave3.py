"""Regression tests for Wave 3 Python improvements.

Covers:
  PY-W3-1  Attribute callbacks in argument positions emit reference edges
  PY-W3-2  Attribute callbacks in list/tuple literals emit reference edges
  PY-W3-3  Keyword argument attribute values emit reference edges
  PY-W3-4  Dict value attributes emit reference edges
  PY-W3-5  self.x / cls.x attributes in arguments are NOT emitted (noise filter)
  PY-W3-6  @validator kind for Pydantic v1
  PY-W3-7  @field_validator kind for Pydantic v2
  PY-W3-8  @root_validator kind
  PY-W3-9  @model_validator kind
  PY-W3-10 Existing call edges through attribute calls not affected
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "mod.py"):
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


def _ref_targets(refs, kind=None):
    if kind:
        return {r["target_name"] for r in refs if r.get("kind") == kind}
    return {r["target_name"] for r in refs}


# ---------------------------------------------------------------------------
# PY-W3-1  Attribute in call arguments
# ---------------------------------------------------------------------------


def test_py_attribute_ref_in_call_argument():
    """viewsets.UserViewSet passed as call argument must emit a reference edge."""
    source = """\
import viewsets
router.register(r'users', viewsets.UserViewSet)
"""
    _, refs = _parse(source)
    # Should emit a reference to the dotted path or at least to UserViewSet
    all_targets = {r["target_name"] for r in refs}
    assert any("UserViewSet" in t for t in all_targets), f"viewsets.UserViewSet not referenced; got {all_targets}"


# ---------------------------------------------------------------------------
# PY-W3-2  Attribute in list literal
# ---------------------------------------------------------------------------


def test_py_attribute_ref_in_list_literal():
    """Module-qualified class in a list literal must emit a reference."""
    source = """\
MIDDLEWARE = [
    security.RequestValidator,
    logging.RequestLogger,
]
"""
    _, refs = _parse(source)
    all_targets = {r["target_name"] for r in refs}
    assert any("RequestValidator" in t for t in all_targets), (
        f"security.RequestValidator not referenced; got {all_targets}"
    )
    assert any("RequestLogger" in t for t in all_targets), f"logging.RequestLogger not referenced; got {all_targets}"


# ---------------------------------------------------------------------------
# PY-W3-3  Keyword argument attribute value
# ---------------------------------------------------------------------------


def test_py_attribute_ref_in_keyword_argument():
    """view=views.UserListView must emit a reference to the attribute."""
    source = """\
configure_router(view=views.UserListView)
"""
    _, refs = _parse(source)
    all_targets = {r["target_name"] for r in refs}
    assert any("UserListView" in t for t in all_targets), (
        f"views.UserListView not referenced via keyword; got {all_targets}"
    )


# ---------------------------------------------------------------------------
# PY-W3-4  Dict value attribute
# ---------------------------------------------------------------------------


def test_py_attribute_ref_in_dict_value():
    """Dict value `{'handler': views.MyView}` must emit a reference."""
    source = """\
handlers = {'default': views.MyView}
"""
    _, refs = _parse(source)
    all_targets = {r["target_name"] for r in refs}
    assert any("MyView" in t for t in all_targets), f"views.MyView not referenced in dict value; got {all_targets}"


# ---------------------------------------------------------------------------
# PY-W3-5  self.x and cls.x are filtered
# ---------------------------------------------------------------------------


def test_py_self_attribute_not_emitted_as_reference():
    """self.helper passed to a call must NOT emit a spurious reference edge."""
    source = """\
class Foo:
    def run(self):
        process(self.helper)
"""
    _, refs = _parse(source)
    ref_targets = _ref_targets(refs, kind="reference")
    assert "self.helper" not in ref_targets, f"self.helper should not be emitted as reference; got {ref_targets}"


# ---------------------------------------------------------------------------
# PY-W3-6  @validator (Pydantic v1)
# ---------------------------------------------------------------------------


def test_py_pydantic_v1_validator_kind():
    """@validator-decorated methods must get kind='validator'."""
    source = """\
from pydantic import BaseModel, validator

class User(BaseModel):
    name: str

    @validator('name')
    def validate_name(cls, v):
        return v.strip()
"""
    symbols, _ = _parse(source)
    sym = next((s for s in symbols if s["name"] == "validate_name"), None)
    assert sym is not None, "validate_name not found"
    assert sym["kind"] == "validator", f"expected 'validator', got {sym['kind']!r}"


# ---------------------------------------------------------------------------
# PY-W3-7  @field_validator (Pydantic v2)
# ---------------------------------------------------------------------------


def test_py_pydantic_v2_field_validator_kind():
    """@field_validator-decorated methods must get kind='validator'."""
    source = """\
from pydantic import BaseModel, field_validator

class Item(BaseModel):
    price: float

    @field_validator('price')
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('must be > 0')
        return v
"""
    symbols, _ = _parse(source)
    sym = next((s for s in symbols if s["name"] == "must_be_positive"), None)
    assert sym is not None, "must_be_positive not found"
    assert sym["kind"] == "validator", f"expected 'validator', got {sym['kind']!r}"


# ---------------------------------------------------------------------------
# PY-W3-8  @root_validator
# ---------------------------------------------------------------------------


def test_py_pydantic_root_validator_kind():
    """@root_validator-decorated methods must get kind='validator'."""
    source = """\
from pydantic import BaseModel, root_validator

class Config(BaseModel):
    host: str
    port: int

    @root_validator
    def check_config(cls, values):
        return values
"""
    symbols, _ = _parse(source)
    sym = next((s for s in symbols if s["name"] == "check_config"), None)
    assert sym is not None, "check_config not found"
    assert sym["kind"] == "validator", f"expected 'validator', got {sym['kind']!r}"


# ---------------------------------------------------------------------------
# PY-W3-9  @model_validator (Pydantic v2)
# ---------------------------------------------------------------------------


def test_py_pydantic_model_validator_kind():
    """@model_validator-decorated methods must get kind='validator'."""
    source = """\
from pydantic import BaseModel, model_validator

class Request(BaseModel):
    method: str
    url: str

    @model_validator(mode='after')
    def check_url(self):
        return self
"""
    symbols, _ = _parse(source)
    sym = next((s for s in symbols if s["name"] == "check_url"), None)
    assert sym is not None, "check_url not found"
    assert sym["kind"] == "validator", f"expected 'validator', got {sym['kind']!r}"


# ---------------------------------------------------------------------------
# PY-W3-10  Existing call edges not broken
# ---------------------------------------------------------------------------


def test_py_attribute_call_edges_still_work():
    """views.MyView.as_view() call still emits a call edge after attribute-ref fix."""
    source = """\
from django.urls import path
urlpatterns = [
    path('/', views.MyView.as_view()),
]
"""
    _, refs = _parse(source)
    call_targets = _ref_targets(refs, kind="call")
    assert any("as_view" in t for t in call_targets), f"as_view call edge missing; got {call_targets}"
