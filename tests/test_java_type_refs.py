"""Java type-reference and method-reference extraction — Wave 5 (#19, #20).

Covers:
  JA-T1  method reference String::valueOf      → call edge to valueOf
  JA-T2  method reference this::handleRequest  → call edge to handleRequest
  JA-T3  method reference MyClass::new         → call edge to MyClass
  JA-T4  generic field List<User>              → type_ref to User
  JA-T5  generic Map<String, Config>           → type_refs to String and Config
  JA-T6  method return type User getUser()     → type_ref to User
  JA-T7  method param type void process(Request req) → type_ref to Request
  JA-T8  primitive int field                   → no type_ref
  JA-T9  constructor param type                → type_ref
  JA-T10 nested generic List<Map<Key, Val>>    → type_refs to all
  JA-T11 array type User[]                     → type_ref to User
  JA-T12 local variable declaration type       → type_ref
  JA-NR1 plain method call still works        → non-regression
  JA-NR2 import refs still work               → non-regression
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _parse(source_text: str, file_path: str = "Foo.java"):
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES, detect_language
    from roam.languages.registry import get_extractor

    language = detect_language(file_path)
    assert language is not None, f"could not detect language for {file_path}"
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


def _call_targets(refs) -> set[str]:
    return {r["target_name"] for r in refs if r.get("kind") == "call"}


# ---------------------------------------------------------------------------
# JA-T1  method reference → call edge (static method)
# ---------------------------------------------------------------------------


def test_java_method_ref_static():
    """String::valueOf must produce a call edge to valueOf."""
    source = """\
class Foo {
    void setup() {
        list.stream().map(String::valueOf);
    }
}
"""
    _, refs = _parse(source)
    assert "valueOf" in _call_targets(refs), f"valueOf missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T2  method reference → call edge (instance method)
# ---------------------------------------------------------------------------


def test_java_method_ref_instance():
    """this::handleRequest must produce a call edge to handleRequest."""
    source = """\
class Server {
    void register() {
        router.get("/", this::handleRequest);
    }
    void handleRequest() {}
}
"""
    _, refs = _parse(source)
    assert "handleRequest" in _call_targets(refs), f"handleRequest missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T3  method reference → call edge (constructor ref)
# ---------------------------------------------------------------------------


def test_java_method_ref_constructor():
    """MyClass::new must produce a call edge to MyClass."""
    source = """\
class Factory {
    void build() {
        supplier = MyClass::new;
    }
}
"""
    _, refs = _parse(source)
    assert "MyClass" in _call_targets(refs), f"MyClass missing from {_call_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T4  generic field List<User> → type_ref to User
# ---------------------------------------------------------------------------


def test_java_generic_field_type_ref():
    """Generic field type argument must produce a type_ref edge."""
    source = """\
import java.util.List;

class Registry {
    private List<User> users;
}
"""
    _, refs = _parse(source)
    assert "User" in _type_ref_targets(refs), f"User missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T5  generic Map<String, Config> → type_refs to both
# ---------------------------------------------------------------------------


def test_java_generic_map_type_refs():
    """Both type arguments of Map<String, Config> must produce type_ref edges."""
    source = """\
import java.util.Map;

class Cache {
    private Map<String, Config> store;
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "String" in targets, f"String missing from {targets}"
    assert "Config" in targets, f"Config missing from {targets}"


# ---------------------------------------------------------------------------
# JA-T6  method return type → type_ref
# ---------------------------------------------------------------------------


def test_java_method_return_type_ref():
    """Method return type must produce a type_ref edge."""
    source = """\
class Service {
    User getUser(long id) {
        return null;
    }
}
"""
    _, refs = _parse(source)
    assert "User" in _type_ref_targets(refs), f"User missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T7  method parameter type → type_ref
# ---------------------------------------------------------------------------


def test_java_method_param_type_ref():
    """Method parameter type must produce a type_ref edge."""
    source = """\
class Handler {
    void process(Request req) {}
}
"""
    _, refs = _parse(source)
    assert "Request" in _type_ref_targets(refs), f"Request missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T8  primitive int field → no type_ref
# ---------------------------------------------------------------------------


def test_java_primitive_not_emitted():
    """Primitive Java types must not produce type_ref edges."""
    source = """\
class Counter {
    private int count;
    private boolean active;
    private double score;
    private long timestamp;

    void reset(int value) {}
    int getCount() { return count; }
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    primitives = {"int", "boolean", "double", "long", "float", "short", "byte", "char", "void"}
    overlap = targets & primitives
    assert not overlap, f"primitive types leaked into type_refs: {overlap}"


# ---------------------------------------------------------------------------
# JA-T9  constructor parameter type → type_ref
# ---------------------------------------------------------------------------


def test_java_constructor_param_type_ref():
    """Constructor parameter type must produce a type_ref edge."""
    source = """\
class Service {
    Service(Repository repo, Config config) {}
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Repository" in targets, f"Repository missing from {targets}"
    assert "Config" in targets, f"Config missing from {targets}"


# ---------------------------------------------------------------------------
# JA-T10  nested generic List<Map<Key, Val>> → type_refs for all
# ---------------------------------------------------------------------------


def test_java_nested_generic_type_refs():
    """Nested generic type arguments must all produce type_ref edges."""
    source = """\
import java.util.List;
import java.util.Map;

class Store {
    List<Map<Key, Value>> index;
}
"""
    _, refs = _parse(source)
    targets = _type_ref_targets(refs)
    assert "Key" in targets, f"Key missing from {targets}"
    assert "Value" in targets, f"Value missing from {targets}"


# ---------------------------------------------------------------------------
# JA-T11  array type User[] → type_ref to User
# ---------------------------------------------------------------------------


def test_java_array_type_ref():
    """Array element type must produce a type_ref edge."""
    source = """\
class Team {
    User[] members;
}
"""
    _, refs = _parse(source)
    assert "User" in _type_ref_targets(refs), f"User missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-T12  local variable declaration type → type_ref
# ---------------------------------------------------------------------------


def test_java_local_variable_type_ref():
    """Local variable declaration type must produce a type_ref edge."""
    source = """\
class Processor {
    void run() {
        Config cfg = new Config();
    }
}
"""
    _, refs = _parse(source)
    # Config should appear as type_ref (from declaration) and call (from new Config())
    assert "Config" in _type_ref_targets(refs), f"Config missing from {_type_ref_targets(refs)}"


# ---------------------------------------------------------------------------
# JA-NR1  plain method call still works → non-regression
# ---------------------------------------------------------------------------


def test_java_plain_method_call_unaffected():
    """Adding type/method-ref emission must not break plain method call extraction."""
    source = """\
class App {
    void run() {
        service.start();
        logger.log("hello");
    }
}
"""
    _, refs = _parse(source)
    calls = _call_targets(refs)
    assert "service.start" in calls or "start" in calls, f"start missing from {calls}"


# ---------------------------------------------------------------------------
# JA-NR2  import refs still work → non-regression
# ---------------------------------------------------------------------------


def test_java_import_refs_unaffected():
    """Import references must still work alongside type_refs."""
    source = """\
import java.util.List;
import com.example.Config;

class App {}
"""
    _, refs = _parse(source)
    import_targets = {r["target_name"] for r in refs if r.get("kind") == "import"}
    assert "List" in import_targets, f"List import missing from {import_targets}"
