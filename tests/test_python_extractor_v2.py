"""Tests for Python extractor v8.1.1 improvements.

Covers:
1. Instance attribute extraction from __init__ (self.x = ...)
2. Type annotation refs on assignment nodes (class fields + module vars)
3. Forward references (string annotations) in type hints
4. Self-name detection (Pyan-inspired, not hardcoded to "self")
5. Deduplication between class-level and __init__ attributes
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helper: parse Python source and extract symbols + references
# ---------------------------------------------------------------------------

def _parse_py(source_text: str, file_path: str = "example.py"):
    """Parse Python source and return (symbols, references)."""
    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor
    from tree_sitter_language_pack import get_parser

    grammar = GRAMMAR_ALIASES.get("python", "python")
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor("python")
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _sym_names(symbols, kind=None, parent=None):
    """Get symbol names, optionally filtered by kind and/or parent."""
    result = []
    for s in symbols:
        if kind and s["kind"] != kind:
            continue
        if parent is not None and s.get("parent_name") != parent:
            continue
        result.append(s["name"])
    return result


def _ref_targets(refs, kind=None, source_name=None):
    """Get reference target_names, optionally filtered by kind and/or source_name."""
    result = []
    for r in refs:
        if kind and r["kind"] != kind:
            continue
        if source_name is not None and r.get("source_name") != source_name:
            continue
        result.append(r["target_name"])
    return result


# ===========================================================================
# 1. Instance attribute extraction from __init__
# ===========================================================================

class TestInitAttributes:
    """Test extraction of self.x = ... assignments in __init__."""

    def test_basic_self_attrs(self):
        """Simple self.x = value in __init__ should produce property symbols."""
        src = (
            "class Dog:\n"
            "    def __init__(self, name, age):\n"
            "        self.name = name\n"
            "        self.age = age\n"
            "        self.alive = True\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Dog")
        assert "name" in props
        assert "age" in props
        assert "alive" in props

    def test_private_attrs(self):
        """Private self._x attrs should be extracted with private visibility."""
        src = (
            "class Conn:\n"
            "    def __init__(self):\n"
            "        self._socket = None\n"
            "        self.__secret = 42\n"
        )
        syms, _ = _parse_py(src)
        props = [s for s in syms if s["kind"] == "property" and s["parent_name"] == "Conn"]
        names = {s["name"]: s["visibility"] for s in props}
        assert "_socket" in names
        assert "__secret" in names
        assert names["_socket"] == "private"
        assert names["__secret"] == "private"

    def test_qualified_name(self):
        """Instance attrs should have qualified names like ClassName.attr."""
        src = (
            "class Foo:\n"
            "    def __init__(self):\n"
            "        self.bar = 1\n"
        )
        syms, _ = _parse_py(src)
        props = [s for s in syms if s["kind"] == "property" and s["name"] == "bar"]
        assert len(props) == 1
        assert props[0]["qualified_name"] == "Foo.bar"

    def test_attrs_in_conditional(self):
        """self.x inside if/try blocks in __init__ should be extracted."""
        src = (
            "class Parser:\n"
            "    def __init__(self, strict=False):\n"
            "        self.strict = strict\n"
            "        if strict:\n"
            "            self.validator = Validator()\n"
            "        try:\n"
            "            self.cache = load_cache()\n"
            "        except Exception:\n"
            "            self.cache = {}\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Parser")
        assert "strict" in props
        assert "validator" in props
        assert "cache" in props

    def test_dedup_first_wins(self):
        """If self.x appears twice in __init__, only the first is extracted."""
        src = (
            "class Buf:\n"
            "    def __init__(self):\n"
            "        self.data = None\n"
            "        self.data = []\n"
        )
        syms, _ = _parse_py(src)
        props = [s for s in syms if s["kind"] == "property" and s["name"] == "data"]
        assert len(props) == 1

    def test_skip_nested_attr(self):
        """self.nested.attr should NOT be extracted (not a direct instance attr)."""
        src = (
            "class Graph:\n"
            "    def __init__(self):\n"
            "        self.nodes = []\n"
            "        self.meta.version = 2\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Graph")
        assert "nodes" in props
        assert "version" not in props

    def test_default_value_extraction(self):
        """Literal default values should be captured."""
        src = (
            "class Config:\n"
            "    def __init__(self):\n"
            "        self.timeout = 30\n"
            "        self.name = 'default'\n"
            "        self.debug = False\n"
        )
        syms, _ = _parse_py(src)
        props = {s["name"]: s for s in syms
                 if s["kind"] == "property" and s["parent_name"] == "Config"}
        assert props["timeout"]["default_value"] == "30"
        assert props["debug"]["default_value"] == "False"

    def test_no_attrs_from_regular_methods(self):
        """self.x in methods other than __init__ should NOT produce symbols."""
        src = (
            "class Server:\n"
            "    def __init__(self):\n"
            "        self.port = 8080\n"
            "    def start(self):\n"
            "        self.running = True\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Server")
        assert "port" in props
        assert "running" not in props

    def test_empty_init(self):
        """__init__ with no self.x assignments should produce no extra symbols."""
        src = (
            "class Empty:\n"
            "    def __init__(self):\n"
            "        pass\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Empty")
        assert props == []


# ===========================================================================
# 2. Self-name detection (Pyan-inspired)
# ===========================================================================

class TestSelfNameDetection:
    """Test that the first __init__ param is used, not hardcoded 'self'."""

    def test_cls_as_self(self):
        """Classes using 'cls' as first param should still extract attrs."""
        src = (
            "class Meta:\n"
            "    def __init__(cls, name):\n"
            "        cls.name = name\n"
            "        cls.registry = {}\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Meta")
        assert "name" in props
        assert "registry" in props

    def test_this_as_self(self):
        """Classes using 'this' as first param should work."""
        src = (
            "class Widget:\n"
            "    def __init__(this):\n"
            "        this.visible = True\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Widget")
        assert "visible" in props

    def test_typed_self_param(self):
        """Typed self param like (self: Self) should still be detected."""
        src = (
            "class Typed:\n"
            "    def __init__(self: 'Typed', val: int):\n"
            "        self.val = val\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="Typed")
        assert "val" in props


# ===========================================================================
# 3. Deduplication with class-level properties
# ===========================================================================

class TestAttrDeduplication:
    """Test that class-level + __init__ attrs don't produce duplicates."""

    def test_class_level_wins(self):
        """If class has both x: str and self.x = value, only one property exists."""
        src = (
            "class Model:\n"
            "    name: str\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
        )
        syms, _ = _parse_py(src)
        props = [s for s in syms if s["kind"] == "property" and s["name"] == "name"
                 and s["parent_name"] == "Model"]
        assert len(props) == 1

    def test_mixed_attrs(self):
        """Class-level and __init__-only attrs should both appear without duplicates."""
        src = (
            "class User:\n"
            "    id: int\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "        self.id = generate_id()\n"
        )
        syms, _ = _parse_py(src)
        props = _sym_names(syms, kind="property", parent="User")
        assert "id" in props
        assert "name" in props
        # No duplicates
        assert len([p for p in props if p == "id"]) == 1


# ===========================================================================
# 4. Type annotation refs on assignments
# ===========================================================================

class TestAssignmentTypeRefs:
    """Test that type annotations on assignments produce type_ref edges."""

    def test_class_field_type_ref(self):
        """Class field `x: Path` should produce type_ref to Path."""
        src = (
            "class Config:\n"
            "    path: Path\n"
            "    name: str\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Path" in type_refs
        # str is a builtin, should NOT be in type_refs
        assert "str" not in type_refs

    def test_generic_class_field_type_ref(self):
        """Class field `items: List[Config]` should produce type_ref to both List and Config."""
        src = (
            "class Manager:\n"
            "    items: List[Config]\n"
            "    cache: Dict[str, Entry] = {}\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "List" in type_refs
        assert "Config" in type_refs
        assert "Dict" in type_refs
        assert "Entry" in type_refs

    def test_optional_class_field(self):
        """Optional[Config] should produce type_ref to Optional and Config."""
        src = (
            "class Node:\n"
            "    parent: Optional[Node] = None\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Optional" in type_refs
        assert "Node" in type_refs

    def test_module_level_annotated_var(self):
        """Module-level `cache: Dict[str, Config] = {}` should produce type_ref."""
        src = (
            "cache: Dict[str, Config] = {}\n"
            "logger: Logger = get_logger()\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Dict" in type_refs
        assert "Config" in type_refs
        assert "Logger" in type_refs

    def test_unannotated_assignment_no_type_ref(self):
        """Plain `x = value` (no annotation) should NOT produce type_ref."""
        src = (
            "class Foo:\n"
            "    x = 42\n"
            "    y = 'hello'\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        # No type refs from unannotated assignments
        assert type_refs == []


# ===========================================================================
# 5. Forward references (string annotations)
# ===========================================================================

class TestForwardReferences:
    """Test that string annotations like 'Config' produce type_ref edges."""

    def test_simple_forward_ref(self):
        """Parameter annotation 'Config' should produce type_ref."""
        src = (
            "def process(item: 'Config') -> None:\n"
            "    pass\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Config" in type_refs

    def test_forward_ref_in_optional(self):
        """Optional['Node'] should produce type_ref to both Optional and Node."""
        src = (
            "class Tree:\n"
            "    parent: Optional['Tree'] = None\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Optional" in type_refs
        assert "Tree" in type_refs

    def test_dotted_forward_ref(self):
        """'module.ClassName' forward ref should be extracted."""
        src = (
            "def create() -> 'models.User':\n"
            "    pass\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "models.User" in type_refs

    def test_non_identifier_string_skipped(self):
        """Arbitrary strings in annotations should NOT produce type_ref."""
        src = (
            "def f(x: 'not a valid type hint 123') -> None:\n"
            "    pass\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        # The string is not an identifier, should be skipped
        assert "not a valid type hint 123" not in type_refs

    def test_builtin_string_skipped(self):
        """Forward ref to builtin like 'int' should be skipped."""
        src = (
            "def f(x: 'int') -> 'str':\n"
            "    pass\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "int" not in type_refs
        assert "str" not in type_refs

    def test_forward_ref_on_class_field(self):
        """Class field with forward ref annotation should produce type_ref."""
        src = (
            "class LinkedList:\n"
            "    next: 'LinkedList'\n"
        )
        _, refs = _parse_py(src)
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "LinkedList" in type_refs


# ===========================================================================
# 6. Integration: all features together
# ===========================================================================

class TestIntegrated:
    """Test all three improvements working together on realistic code."""

    def test_dataclass_like_pattern(self):
        """Dataclass-like class with annotations and __init__ self-assignments."""
        src = (
            "class Config:\n"
            "    path: Path\n"
            "    items: List['Entry']\n"
            "    debug: bool = False\n"
            "\n"
            "    def __init__(self, path, items=None):\n"
            "        self.path = path\n"
            "        self.items = items or []\n"
            "        self._cache = {}\n"
        )
        syms, refs = _parse_py(src)

        # Symbols: class + __init__ method + class-level props + __init__ attrs (deduped)
        props = _sym_names(syms, kind="property", parent="Config")
        assert "path" in props
        assert "items" in props
        assert "debug" in props
        assert "_cache" in props
        # No duplicates for path/items (class-level wins)
        assert len([p for p in props if p == "path"]) == 1
        assert len([p for p in props if p == "items"]) == 1

        # References: type_refs from class field annotations
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Path" in type_refs
        assert "List" in type_refs
        assert "Entry" in type_refs  # forward ref

    def test_realistic_class(self):
        """Realistic class with inheritance, decorators, type hints, and self attrs."""
        src = (
            "from pathlib import Path\n"
            "\n"
            "class FileProcessor(BaseProcessor):\n"
            "    \"\"\"Process files.\"\"\"\n"
            "    encoding: str = 'utf-8'\n"
            "\n"
            "    def __init__(self, root: Path, strict: bool = False):\n"
            "        self.root = root\n"
            "        self.strict = strict\n"
            "        self._results: List['Result'] = []\n"
            "\n"
            "    def process(self, path: Path) -> 'Result':\n"
            "        return Result(path)\n"
        )
        syms, refs = _parse_py(src)

        # Instance attrs
        props = _sym_names(syms, kind="property", parent="FileProcessor")
        assert "encoding" in props
        assert "root" in props
        assert "strict" in props
        assert "_results" in props

        # Type refs from function params + return type + class fields
        type_refs = _ref_targets(refs, kind="type_ref")
        assert "Path" in type_refs
        # Forward ref from return type
        assert "Result" in type_refs
