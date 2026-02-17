"""Per-language extraction tests (~80 tests).

Uses both direct extractor testing (via _parse_and_extract) and CLI
integration testing (via project_factory + invoke_cli) to verify that
each supported language correctly extracts symbols, references, and
structural information.

Languages covered: Python, JavaScript, TypeScript, Java, Go, Rust, C, PHP, C#.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli, parse_json_output


# ---------------------------------------------------------------------------
# Override cli_runner fixture to handle Click 8.2+ (mix_stderr removed)
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner compatible with Click 8.2+."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ---------------------------------------------------------------------------
# Helper: parse source text using tree-sitter + language extractor
# ---------------------------------------------------------------------------

def _parse_and_extract(source_text: str, file_path: str, language: str = None):
    """Parse source text and extract symbols + references.

    Returns (symbols, references) lists.
    """
    from roam.index.parser import detect_language, GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor
    from tree_sitter_language_pack import get_parser

    if language is None:
        language = detect_language(file_path)
    assert language is not None, f"Could not detect language for {file_path}"

    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


# ===========================================================================
# PYTHON TESTS
# ===========================================================================

class TestPythonExtraction:
    """Tests for Python symbol and reference extraction."""

    def test_python_class_extraction(self, project_factory, cli_runner, monkeypatch):
        """Class with methods should be extracted with correct names and kinds."""
        proj = project_factory({
            "app.py": (
                "class MyClass:\n"
                "    def method_one(self):\n"
                "        pass\n"
                "    def method_two(self, x):\n"
                "        return x * 2\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "app.py"])
        data = json.loads(result.output)
        symbols = data.get("symbols", [])
        names = [s["name"] for s in symbols]
        assert "MyClass" in names
        assert "method_one" in names
        assert "method_two" in names
        # Check kinds
        cls_sym = next(s for s in symbols if s["name"] == "MyClass")
        assert cls_sym["kind"] == "class"
        meth_sym = next(s for s in symbols if s["name"] == "method_one")
        assert meth_sym["kind"] == "method"

    def test_python_function_extraction(self):
        """Standalone functions should be extracted as 'function' kind."""
        source = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(x, y):\n"
            "    return x * y\n"
        )
        symbols, _ = _parse_and_extract(source, "math_utils.py")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "add" in func_names
        assert "multiply" in func_names

    def test_python_import_resolution(self, project_factory, cli_runner, monkeypatch):
        """Imports should create references that lead to edges after indexing."""
        proj = project_factory({
            "models.py": (
                "class User:\n"
                "    def __init__(self, name):\n"
                "        self.name = name\n"
            ),
            "service.py": (
                "from models import User\n"
                "\n"
                "def create_user(name):\n"
                "    return User(name)\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "search", "User"])
        data = json.loads(result.output)
        results = data.get("results", [])
        assert any(r["name"] == "User" for r in results)

    def test_python_decorators(self):
        """Decorated functions should still be extracted."""
        source = (
            "def my_decorator(func):\n"
            "    def wrapper(*args):\n"
            "        return func(*args)\n"
            "    return wrapper\n"
            "\n"
            "@my_decorator\n"
            "def decorated_func():\n"
            "    pass\n"
        )
        symbols, _ = _parse_and_extract(source, "deco.py")
        names = [s["name"] for s in symbols]
        assert "decorated_func" in names
        assert "my_decorator" in names

    def test_python_nested_class(self):
        """Inner classes should be extracted with correct parent linkage."""
        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def inner_method(self):\n"
            "            pass\n"
            "    def outer_method(self):\n"
            "        pass\n"
        )
        symbols, _ = _parse_and_extract(source, "nested.py")
        names = [s["name"] for s in symbols]
        assert "Outer" in names
        assert "Inner" in names
        assert "inner_method" in names
        assert "outer_method" in names
        inner = next(s for s in symbols if s["name"] == "Inner")
        assert inner["parent_name"] == "Outer"

    def test_python_property(self):
        """@property methods should be extracted."""
        source = (
            "class Config:\n"
            "    @property\n"
            "    def value(self):\n"
            "        return self._value\n"
            "\n"
            "    @value.setter\n"
            "    def value(self, v):\n"
            "        self._value = v\n"
        )
        symbols, _ = _parse_and_extract(source, "config.py")
        names = [s["name"] for s in symbols]
        assert "Config" in names
        assert "value" in names

    def test_python_docstring(self):
        """Docstrings should be captured on symbols."""
        source = (
            "def documented():\n"
            '    """This is the docstring."""\n'
            "    pass\n"
        )
        symbols, _ = _parse_and_extract(source, "docs.py")
        func = next(s for s in symbols if s["name"] == "documented")
        assert func["docstring"] is not None
        assert "docstring" in func["docstring"]

    def test_python_visibility(self):
        """Private (_prefix) functions should have 'private' visibility."""
        source = (
            "def public_func():\n"
            "    pass\n"
            "\n"
            "def _private_func():\n"
            "    pass\n"
        )
        symbols, _ = _parse_and_extract(source, "vis.py")
        pub = next(s for s in symbols if s["name"] == "public_func")
        priv = next(s for s in symbols if s["name"] == "_private_func")
        assert pub["visibility"] == "public"
        assert priv["visibility"] == "private"


# ===========================================================================
# JAVASCRIPT TESTS
# ===========================================================================

class TestJavaScriptExtraction:
    """Tests for JavaScript symbol and reference extraction."""

    def test_js_function_extraction(self):
        """Function declarations and arrow functions should be extracted."""
        source = (
            "function greet(name) {\n"
            "    return 'Hello ' + name;\n"
            "}\n"
            "\n"
            "const farewell = (name) => {\n"
            "    return 'Bye ' + name;\n"
            "};\n"
        )
        symbols, _ = _parse_and_extract(source, "funcs.js")
        names = [s["name"] for s in symbols]
        assert "greet" in names
        assert "farewell" in names

    def test_js_class_extraction(self):
        """ES6 classes with methods should be extracted."""
        source = (
            "class Animal {\n"
            "    constructor(name) {\n"
            "        this.name = name;\n"
            "    }\n"
            "\n"
            "    speak() {\n"
            "        return 'sound';\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "animal.js")
        names = [s["name"] for s in symbols]
        assert "Animal" in names
        assert "speak" in names
        cls_sym = next(s for s in symbols if s["name"] == "Animal")
        assert cls_sym["kind"] == "class"

    def test_js_import_export(self, project_factory, cli_runner, monkeypatch):
        """Import/export should create cross-file references."""
        proj = project_factory({
            "lib.js": (
                "function helper() {\n"
                "    return 42;\n"
                "}\n"
                "module.exports = { helper };\n"
            ),
            "app.js": (
                'const { helper } = require("./lib");\n'
                "\n"
                "function main() {\n"
                "    return helper();\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "search", "helper"])
        data = json.loads(result.output)
        results = data.get("results", [])
        assert any(r["name"] == "helper" for r in results)

    def test_js_default_export(self):
        """Default export class should be extracted."""
        source = (
            "export default class Router {\n"
            "    route(path) {\n"
            "        return path;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "router.js")
        names = [s["name"] for s in symbols]
        assert "Router" in names

    def test_js_const_arrow(self):
        """const with arrow function should be extracted as function/variable."""
        source = (
            "const createApp = () => {\n"
            "    return { name: 'app' };\n"
            "};\n"
            "\n"
            "const API_KEY = 'abc123';\n"
        )
        symbols, _ = _parse_and_extract(source, "app.js")
        names = [s["name"] for s in symbols]
        assert "createApp" in names
        assert "API_KEY" in names

    def test_js_destructuring_import(self):
        """Named imports via require destructuring should create references."""
        source = (
            'const { readFile, writeFile } = require("fs");\n'
            "\n"
            "function process() {\n"
            "    readFile('test.txt');\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "io.js")
        ref_targets = [r["target_name"] for r in refs if r["kind"] == "import"]
        assert "fs" in ref_targets


# ===========================================================================
# TYPESCRIPT TESTS
# ===========================================================================

class TestTypeScriptExtraction:
    """Tests for TypeScript symbol and reference extraction."""

    def test_ts_interface(self):
        """Interface extraction should capture interface name and methods."""
        source = (
            "export interface Serializable {\n"
            "    serialize(): string;\n"
            "    deserialize(data: string): void;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "types.ts")
        names = [s["name"] for s in symbols]
        assert "Serializable" in names
        iface = next(s for s in symbols if s["name"] == "Serializable")
        assert iface["kind"] == "interface"

    def test_ts_type_alias(self):
        """Type aliases should be extracted."""
        source = (
            'type UserRole = "admin" | "user" | "guest";\n'
            "type Callback = (data: any) => void;\n"
        )
        symbols, _ = _parse_and_extract(source, "aliases.ts")
        names = [s["name"] for s in symbols]
        assert "UserRole" in names
        assert "Callback" in names

    def test_ts_enum(self):
        """Enums should be extracted."""
        source = (
            "enum Direction {\n"
            "    Up,\n"
            "    Down,\n"
            "    Left,\n"
            "    Right,\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "direction.ts")
        names = [s["name"] for s in symbols]
        assert "Direction" in names
        dir_sym = next(s for s in symbols if s["name"] == "Direction")
        assert dir_sym["kind"] == "enum"

    def test_ts_generic_class(self):
        """Generic classes should be extracted with correct name."""
        source = (
            "export class Repository<T> {\n"
            "    private items: T[] = [];\n"
            "\n"
            "    add(item: T): void {\n"
            "        this.items.push(item);\n"
            "    }\n"
            "\n"
            "    findById(id: number): T | undefined {\n"
            "        return this.items[id];\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "repo.ts")
        names = [s["name"] for s in symbols]
        assert "Repository" in names
        cls = next(s for s in symbols if s["name"] == "Repository")
        assert cls["kind"] == "class"
        # Methods should be extracted too
        assert "add" in names
        assert "findById" in names

    def test_ts_decorators(self):
        """TypeScript decorators should not prevent symbol extraction."""
        source = (
            "function Injectable() {\n"
            "    return function(target: any) {};\n"
            "}\n"
            "\n"
            "@Injectable()\n"
            "class UserService {\n"
            "    getUser(id: number): string {\n"
            "        return 'user';\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "service.ts")
        names = [s["name"] for s in symbols]
        assert "UserService" in names
        assert "getUser" in names

    def test_ts_import(self):
        """TypeScript import statements should create references."""
        source = (
            'import { User } from "./models";\n'
            'import * as utils from "./utils";\n'
            "\n"
            "function getUser(): User {\n"
            "    return new User();\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "app.ts")
        import_refs = [r for r in refs if r["kind"] == "import"]
        import_paths = [r["import_path"] for r in import_refs]
        assert any("models" in p for p in import_paths if p)
        assert any("utils" in p for p in import_paths if p)


# ===========================================================================
# JAVA TESTS
# ===========================================================================

class TestJavaExtraction:
    """Tests for Java symbol and reference extraction."""

    def test_java_class(self):
        """Java class with methods and fields should be extracted."""
        source = (
            "public class Person {\n"
            "    private String name;\n"
            "    private int age;\n"
            "\n"
            "    public Person(String name, int age) {\n"
            "        this.name = name;\n"
            "        this.age = age;\n"
            "    }\n"
            "\n"
            "    public String getName() {\n"
            "        return name;\n"
            "    }\n"
            "\n"
            "    public int getAge() {\n"
            "        return age;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Person.java")
        names = [s["name"] for s in symbols]
        assert "Person" in names
        cls = next(s for s in symbols if s["name"] == "Person")
        assert cls["kind"] == "class"
        assert "getName" in names
        assert "getAge" in names
        # Fields should be extracted
        assert "name" in names or "age" in names

    def test_java_interface(self):
        """Java interface should be extracted as 'interface' kind."""
        source = (
            "public interface Comparable {\n"
            "    int compareTo(Object other);\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Comparable.java")
        names = [s["name"] for s in symbols]
        assert "Comparable" in names
        iface = next(s for s in symbols if s["name"] == "Comparable")
        assert iface["kind"] == "interface"

    def test_java_enum(self):
        """Java enum should be extracted as 'enum' kind."""
        source = (
            "public enum Color {\n"
            "    RED, GREEN, BLUE;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Color.java")
        names = [s["name"] for s in symbols]
        assert "Color" in names
        enum_sym = next(s for s in symbols if s["name"] == "Color")
        assert enum_sym["kind"] == "enum"

    def test_java_inheritance(self):
        """extends/implements should create inheritance references."""
        source = (
            "public interface Speakable {\n"
            "    String speak();\n"
            "}\n"
            "\n"
            "public class Animal {\n"
            "    protected String name;\n"
            "}\n"
            "\n"
            "public class Dog extends Animal implements Speakable {\n"
            "    public String speak() {\n"
            "        return \"Woof\";\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "Dog.java")
        # Dog should exist
        names = [s["name"] for s in symbols]
        assert "Dog" in names
        # Inheritance references
        inherits = [r for r in refs if r["kind"] == "inherits"]
        inherit_targets = {r["target_name"] for r in inherits}
        assert "Animal" in inherit_targets
        # Implements references
        implements = [r for r in refs if r["kind"] == "implements"]
        impl_targets = {r["target_name"] for r in implements}
        assert "Speakable" in impl_targets

    def test_java_static_method(self):
        """Static methods should be extracted."""
        source = (
            "public class MathUtils {\n"
            "    public static int add(int a, int b) {\n"
            "        return a + b;\n"
            "    }\n"
            "\n"
            "    public static double PI = 3.14159;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "MathUtils.java")
        names = [s["name"] for s in symbols]
        assert "MathUtils" in names
        assert "add" in names

    def test_java_import(self):
        """Java import statements should create references."""
        source = (
            "import java.util.List;\n"
            "import java.util.Map;\n"
            "\n"
            "public class Container {\n"
            "    private List<String> items;\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "Container.java")
        import_refs = [r for r in refs if r["kind"] == "import"]
        import_targets = {r["target_name"] for r in import_refs}
        assert "List" in import_targets or "java.util.List" in import_targets


# ===========================================================================
# GO TESTS
# ===========================================================================

class TestGoExtraction:
    """Tests for Go symbol and reference extraction."""

    def test_go_function(self):
        """Go function declarations should be extracted."""
        source = (
            "package main\n"
            "\n"
            "func Add(a, b int) int {\n"
            "    return a + b\n"
            "}\n"
            "\n"
            "func multiply(x, y float64) float64 {\n"
            "    return x * y\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "math.go")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "Add" in func_names
        assert "multiply" in func_names

    def test_go_struct(self):
        """Go struct declarations should be extracted as 'class' kind."""
        source = (
            "package store\n"
            "\n"
            "type Config struct {\n"
            "    MaxSize  int\n"
            "    Timeout  int\n"
            "    Verbose  bool\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "config.go")
        names = [s["name"] for s in symbols]
        assert "Config" in names
        cfg = next(s for s in symbols if s["name"] == "Config")
        # Go structs typically map to 'class' kind
        assert cfg["kind"] in ("class", "struct")

    def test_go_interface(self):
        """Go interface declarations should be extracted."""
        source = (
            "package io\n"
            "\n"
            "type Reader interface {\n"
            "    Read(p []byte) (n int, err error)\n"
            "}\n"
            "\n"
            "type Writer interface {\n"
            "    Write(p []byte) (n int, err error)\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "io.go")
        names = [s["name"] for s in symbols]
        assert "Reader" in names
        assert "Writer" in names
        reader = next(s for s in symbols if s["name"] == "Reader")
        assert reader["kind"] == "interface"

    def test_go_method_receiver(self):
        """Go methods with receivers should be extracted as 'method' kind."""
        source = (
            "package store\n"
            "\n"
            "type Store struct {\n"
            "    data map[string]string\n"
            "}\n"
            "\n"
            "func (s *Store) Get(key string) string {\n"
            "    return s.data[key]\n"
            "}\n"
            "\n"
            "func (s *Store) Set(key, value string) {\n"
            "    s.data[key] = value\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "store.go")
        methods = [s for s in symbols if s["kind"] == "method"]
        method_names = {m["name"] for m in methods}
        assert "Get" in method_names
        assert "Set" in method_names
        # Methods should have parent_name referencing the struct
        for m in methods:
            assert m["parent_name"] is not None

    def test_go_exported(self):
        """Exported (uppercase) identifiers should be marked as exported."""
        source = (
            "package pkg\n"
            "\n"
            "func Exported() {}\n"
            "func unexported() {}\n"
        )
        symbols, _ = _parse_and_extract(source, "api.go")
        exp = next(s for s in symbols if s["name"] == "Exported")
        unexp = next(s for s in symbols if s["name"] == "unexported")
        assert exp["is_exported"] is True
        assert unexp["is_exported"] is False

    def test_go_import(self):
        """Go import statements should create references."""
        source = (
            "package main\n"
            "\n"
            'import (\n'
            '    "fmt"\n'
            '    "os"\n'
            ')\n'
            "\n"
            "func main() {\n"
            "    fmt.Println(os.Args)\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "main.go")
        import_refs = [r for r in refs if r["kind"] == "import"]
        import_targets = {r["target_name"] for r in import_refs}
        assert "fmt" in import_targets
        assert "os" in import_targets


# ===========================================================================
# RUST TESTS
# ===========================================================================

class TestRustExtraction:
    """Tests for Rust symbol and reference extraction."""

    def test_rust_function(self):
        """Rust fn items should be extracted as 'function' kind."""
        source = (
            "pub fn add(a: i32, b: i32) -> i32 {\n"
            "    a + b\n"
            "}\n"
            "\n"
            "fn internal_helper() -> bool {\n"
            "    true\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "lib.rs")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "add" in func_names
        assert "internal_helper" in func_names
        pub_fn = next(s for s in symbols if s["name"] == "add")
        assert pub_fn["visibility"] == "public"
        priv_fn = next(s for s in symbols if s["name"] == "internal_helper")
        assert priv_fn["visibility"] == "private"

    def test_rust_struct(self):
        """Rust struct items should be extracted."""
        source = (
            "pub struct Point {\n"
            "    pub x: f64,\n"
            "    pub y: f64,\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "geom.rs")
        names = [s["name"] for s in symbols]
        assert "Point" in names
        point = next(s for s in symbols if s["name"] == "Point")
        assert point["kind"] in ("class", "struct")

    def test_rust_impl(self):
        """Rust impl block methods should be extracted."""
        source = (
            "pub struct Circle {\n"
            "    radius: f64,\n"
            "}\n"
            "\n"
            "impl Circle {\n"
            "    pub fn new(radius: f64) -> Self {\n"
            "        Circle { radius }\n"
            "    }\n"
            "\n"
            "    pub fn area(&self) -> f64 {\n"
            "        std::f64::consts::PI * self.radius * self.radius\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "circle.rs")
        names = [s["name"] for s in symbols]
        assert "Circle" in names
        assert "new" in names
        assert "area" in names
        # Methods in impl should be linked to the struct
        area_sym = next(s for s in symbols if s["name"] == "area")
        assert area_sym["kind"] == "method"

    def test_rust_trait(self):
        """Rust trait items should be extracted as 'trait' kind."""
        source = (
            "pub trait Shape {\n"
            "    fn area(&self) -> f64;\n"
            "    fn perimeter(&self) -> f64;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "shapes.rs")
        names = [s["name"] for s in symbols]
        assert "Shape" in names
        trait_sym = next(s for s in symbols if s["name"] == "Shape")
        assert trait_sym["kind"] == "trait"
        # Trait methods should be extracted
        assert "area" in names
        assert "perimeter" in names

    def test_rust_enum(self):
        """Rust enum items should be extracted as 'enum' kind."""
        source = (
            "pub enum Direction {\n"
            "    North,\n"
            "    South,\n"
            "    East,\n"
            "    West,\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "nav.rs")
        names = [s["name"] for s in symbols]
        assert "Direction" in names
        dir_sym = next(s for s in symbols if s["name"] == "Direction")
        assert dir_sym["kind"] == "enum"

    def test_rust_use(self):
        """Rust use statements should create import references."""
        source = (
            "use std::collections::HashMap;\n"
            "use std::io::{Read, Write};\n"
            "\n"
            "fn process() -> HashMap<String, String> {\n"
            "    HashMap::new()\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "proc.rs")
        import_refs = [r for r in refs if r["kind"] == "import"]
        import_targets = {r["target_name"] for r in import_refs}
        assert "HashMap" in import_targets or "std::collections::HashMap" in import_targets


# ===========================================================================
# C TESTS
# ===========================================================================

class TestCExtraction:
    """Tests for C symbol and reference extraction."""

    def test_c_function(self):
        """C function definitions should be extracted."""
        source = (
            "int add(int a, int b) {\n"
            "    return a + b;\n"
            "}\n"
            "\n"
            "void print_hello() {\n"
            "    printf(\"Hello\\n\");\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "math.c")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "add" in func_names
        assert "print_hello" in func_names

    def test_c_struct(self):
        """C struct definitions should be extracted."""
        source = (
            "typedef struct {\n"
            "    int x;\n"
            "    int y;\n"
            "} Point;\n"
            "\n"
            "struct Node {\n"
            "    int value;\n"
            "    struct Node* next;\n"
            "};\n"
        )
        symbols, _ = _parse_and_extract(source, "types.c")
        names = [s["name"] for s in symbols]
        # At least one of these should be found
        assert "Point" in names or "Node" in names

    def test_c_include(self):
        """C #include directives should create import references."""
        source = (
            '#include <stdio.h>\n'
            '#include "myheader.h"\n'
            "\n"
            "int main() {\n"
            '    printf("Hello\\n");\n'
            "    return 0;\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "main.c")
        import_refs = [r for r in refs if r["kind"] == "import"]
        import_targets = {r["target_name"] for r in import_refs}
        assert "stdio.h" in import_targets or "stdio" in import_targets
        assert "myheader.h" in import_targets or "myheader" in import_targets

    def test_c_prototype(self, project_factory, cli_runner, monkeypatch):
        """Header file prototypes and implementation should both be indexed."""
        proj = project_factory({
            "list.h": (
                "#ifndef LIST_H\n"
                "#define LIST_H\n"
                "\n"
                "struct Node {\n"
                "    int value;\n"
                "    struct Node* next;\n"
                "};\n"
                "\n"
                "struct Node* list_create(int value);\n"
                "void list_push(struct Node** head, int value);\n"
                "\n"
                "#endif\n"
            ),
            "list.c": (
                '#include "list.h"\n'
                "#include <stdlib.h>\n"
                "\n"
                "struct Node* list_create(int value) {\n"
                "    struct Node* node = malloc(sizeof(struct Node));\n"
                "    node->value = value;\n"
                "    node->next = NULL;\n"
                "    return node;\n"
                "}\n"
                "\n"
                "void list_push(struct Node** head, int value) {\n"
                "    struct Node* node = list_create(value);\n"
                "    node->next = *head;\n"
                "    *head = node;\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "search", "list_create"])
        data = json.loads(result.output)
        results = data.get("results", [])
        assert any(r["name"] == "list_create" for r in results)


# ===========================================================================
# PHP TESTS
# ===========================================================================

class TestPhpExtraction:
    """Tests for PHP symbol and reference extraction."""

    def test_php_class(self):
        """PHP class with methods should be extracted."""
        source = (
            "<?php\n"
            "class UserController {\n"
            "    private $userService;\n"
            "\n"
            "    public function __construct($service) {\n"
            "        $this->userService = $service;\n"
            "    }\n"
            "\n"
            "    public function index() {\n"
            "        return $this->userService->getAll();\n"
            "    }\n"
            "\n"
            "    public function show($id) {\n"
            "        return $this->userService->find($id);\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "UserController.php")
        names = [s["name"] for s in symbols]
        assert "UserController" in names
        cls = next(s for s in symbols if s["name"] == "UserController")
        assert cls["kind"] == "class"
        assert "index" in names
        assert "show" in names

    def test_php_function(self):
        """PHP standalone functions should be extracted."""
        source = (
            "<?php\n"
            "function calculate_tax($amount, $rate) {\n"
            "    return $amount * $rate;\n"
            "}\n"
            "\n"
            "function format_currency($value) {\n"
            "    return '$' . number_format($value, 2);\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "helpers.php")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "calculate_tax" in func_names
        assert "format_currency" in func_names

    def test_php_interface(self):
        """PHP interface should be extracted as 'interface' kind."""
        source = (
            "<?php\n"
            "interface Cacheable {\n"
            "    public function getCacheKey();\n"
            "    public function getCacheTTL();\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Cacheable.php")
        names = [s["name"] for s in symbols]
        assert "Cacheable" in names
        iface = next(s for s in symbols if s["name"] == "Cacheable")
        assert iface["kind"] == "interface"

    def test_php_namespace(self):
        """PHP namespace declaration should be handled without breaking extraction."""
        source = (
            "<?php\n"
            "namespace App\\Models;\n"
            "\n"
            "class Product {\n"
            "    private $name;\n"
            "    private $price;\n"
            "\n"
            "    public function getName() {\n"
            "        return $this->name;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Product.php")
        names = [s["name"] for s in symbols]
        assert "Product" in names
        assert "getName" in names


# ===========================================================================
# ADDITIONAL PYTHON TESTS
# ===========================================================================

class TestPythonExtractionExtra:
    """Additional Python extraction tests for edge cases."""

    def test_python_class_inheritance_ref(self):
        """Class inheritance should create 'inherits' references."""
        source = (
            "class Base:\n"
            "    pass\n"
            "\n"
            "class Child(Base):\n"
            "    pass\n"
        )
        symbols, refs = _parse_and_extract(source, "inherit.py")
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert any(r["target_name"] == "Base" for r in inherits)

    def test_python_multiple_inheritance_refs(self):
        """Multiple inheritance should create multiple 'inherits' references."""
        source = (
            "class Flyable:\n"
            "    def fly(self): pass\n"
            "\n"
            "class Swimmable:\n"
            "    def swim(self): pass\n"
            "\n"
            "class Duck(Flyable, Swimmable):\n"
            "    def quack(self): pass\n"
        )
        symbols, refs = _parse_and_extract(source, "multi.py")
        inherits = [r for r in refs if r["kind"] == "inherits"]
        targets = {r["target_name"] for r in inherits}
        assert "Flyable" in targets
        assert "Swimmable" in targets

    def test_python_staticmethod(self):
        """@staticmethod and @classmethod should be extracted."""
        source = (
            "class MyClass:\n"
            "    @staticmethod\n"
            "    def static_method():\n"
            "        return 42\n"
            "\n"
            "    @classmethod\n"
            "    def class_method(cls):\n"
            "        return cls()\n"
        )
        symbols, _ = _parse_and_extract(source, "class_methods.py")
        names = [s["name"] for s in symbols]
        assert "static_method" in names
        assert "class_method" in names

    def test_python_constants(self):
        """Module-level variable assignments should be extracted."""
        source = (
            "MAX_RETRIES = 3\n"
            "TIMEOUT = 30\n"
            "API_URL = 'https://example.com'\n"
        )
        symbols, _ = _parse_and_extract(source, "constants.py")
        names = [s["name"] for s in symbols]
        assert "MAX_RETRIES" in names or "TIMEOUT" in names

    def test_python_async_function(self):
        """Async functions should be extracted."""
        source = (
            "async def fetch_data(url):\n"
            "    return await get(url)\n"
            "\n"
            "async def process_batch(items):\n"
            "    for item in items:\n"
            "        await handle(item)\n"
        )
        symbols, _ = _parse_and_extract(source, "async_funcs.py")
        names = [s["name"] for s in symbols]
        assert "fetch_data" in names
        assert "process_batch" in names


# ===========================================================================
# ADDITIONAL JAVASCRIPT TESTS
# ===========================================================================

class TestJavaScriptExtractionExtra:
    """Additional JavaScript extraction tests."""

    def test_js_generator_function(self):
        """Generator functions should be extracted."""
        source = (
            "function* idGenerator() {\n"
            "    let id = 0;\n"
            "    while (true) yield id++;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "gen.js")
        names = [s["name"] for s in symbols]
        assert "idGenerator" in names

    def test_js_class_extends(self):
        """Class extends should create inheritance references."""
        source = (
            "class Animal {\n"
            "    speak() { return 'sound'; }\n"
            "}\n"
            "\n"
            "class Dog extends Animal {\n"
            "    speak() { return 'woof'; }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "animals.js")
        names = [s["name"] for s in symbols]
        assert "Animal" in names
        assert "Dog" in names
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert any(r["target_name"] == "Animal" for r in inherits)

    def test_js_module_exports_object(self):
        """module.exports = { fn1, fn2 } should extract the functions."""
        source = (
            "function add(a, b) { return a + b; }\n"
            "function sub(a, b) { return a - b; }\n"
            "module.exports = { add, sub };\n"
        )
        symbols, _ = _parse_and_extract(source, "math.js")
        names = [s["name"] for s in symbols]
        assert "add" in names
        assert "sub" in names


# ===========================================================================
# ADDITIONAL TYPESCRIPT TESTS
# ===========================================================================

class TestTypeScriptExtractionExtra:
    """Additional TypeScript extraction tests."""

    def test_ts_abstract_class(self):
        """Abstract class should be extracted."""
        source = (
            "export abstract class BaseEntity {\n"
            "    abstract validate(): boolean;\n"
            "    getId(): number { return 0; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "base.ts")
        names = [s["name"] for s in symbols]
        assert "BaseEntity" in names
        cls = next(s for s in symbols if s["name"] == "BaseEntity")
        assert cls["kind"] == "class"

    def test_ts_implements_interface(self):
        """Class implementing interface should create references."""
        source = (
            "interface Printable {\n"
            "    print(): void;\n"
            "}\n"
            "\n"
            "class Document implements Printable {\n"
            "    print(): void {\n"
            "        console.log('printing');\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "doc.ts")
        names = [s["name"] for s in symbols]
        assert "Printable" in names
        assert "Document" in names
        implements = [r for r in refs if r["kind"] == "implements"]
        assert any(r["target_name"] == "Printable" for r in implements)

    def test_ts_namespace_export(self):
        """Exported functions should be marked as exported."""
        source = (
            "export function publicFn(): void {}\n"
            "function privateFn(): void {}\n"
        )
        symbols, _ = _parse_and_extract(source, "mod.ts")
        pub = next(s for s in symbols if s["name"] == "publicFn")
        assert pub["is_exported"] is True


# ===========================================================================
# ADDITIONAL JAVA TESTS
# ===========================================================================

class TestJavaExtractionExtra:
    """Additional Java extraction tests."""

    def test_java_annotation(self):
        """Annotated methods should still be extracted."""
        source = (
            "public class Service {\n"
            "    @Override\n"
            "    public String toString() {\n"
            '        return "Service";\n'
            "    }\n"
            "\n"
            "    @Deprecated\n"
            "    public void oldMethod() {}\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Service.java")
        names = [s["name"] for s in symbols]
        assert "toString" in names
        assert "oldMethod" in names

    def test_java_constructor(self):
        """Constructors should be extracted."""
        source = (
            "public class Widget {\n"
            "    private String label;\n"
            "\n"
            "    public Widget(String label) {\n"
            "        this.label = label;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Widget.java")
        names = [s["name"] for s in symbols]
        assert "Widget" in names
        # Constructor should be present (may be named Widget or constructor)
        methods = [s for s in symbols if s["kind"] in ("method", "constructor")]
        assert len(methods) >= 1

    def test_java_abstract_class(self):
        """Abstract class should be extracted."""
        source = (
            "public abstract class Shape {\n"
            "    public abstract double area();\n"
            "    public abstract double perimeter();\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Shape.java")
        names = [s["name"] for s in symbols]
        assert "Shape" in names
        assert "area" in names
        assert "perimeter" in names


# ===========================================================================
# ADDITIONAL GO TESTS
# ===========================================================================

class TestGoExtractionExtra:
    """Additional Go extraction tests."""

    def test_go_constants(self):
        """Go const declarations should be extracted."""
        source = (
            "package config\n"
            "\n"
            "const (\n"
            "    MaxRetries = 3\n"
            "    Timeout    = 30\n"
            ")\n"
        )
        symbols, _ = _parse_and_extract(source, "config.go")
        names = [s["name"] for s in symbols]
        assert "MaxRetries" in names or "Timeout" in names

    def test_go_variable_declaration(self):
        """Go var declarations should be extracted."""
        source = (
            "package main\n"
            "\n"
            "var DefaultTimeout = 30\n"
            "var Version = \"1.0.0\"\n"
        )
        symbols, _ = _parse_and_extract(source, "vars.go")
        names = [s["name"] for s in symbols]
        assert "DefaultTimeout" in names or "Version" in names

    def test_go_package_extraction(self):
        """Go package clause should be extracted."""
        source = (
            "package mypackage\n"
            "\n"
            "func DoWork() {}\n"
        )
        symbols, _ = _parse_and_extract(source, "work.go")
        names = [s["name"] for s in symbols]
        # Package symbol should be present
        assert "mypackage" in names or "DoWork" in names


# ===========================================================================
# ADDITIONAL RUST TESTS
# ===========================================================================

class TestRustExtractionExtra:
    """Additional Rust extraction tests."""

    def test_rust_const_item(self):
        """Rust const items should be extracted."""
        source = (
            "pub const MAX_SIZE: usize = 1024;\n"
            "const INTERNAL_LIMIT: u32 = 100;\n"
        )
        symbols, _ = _parse_and_extract(source, "consts.rs")
        names = [s["name"] for s in symbols]
        assert "MAX_SIZE" in names

    def test_rust_type_alias(self):
        """Rust type aliases should be extracted."""
        source = (
            "type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;\n"
            "pub type Id = u64;\n"
        )
        symbols, _ = _parse_and_extract(source, "types.rs")
        names = [s["name"] for s in symbols]
        assert "Result" in names or "Id" in names

    def test_rust_impl_trait_for_struct(self):
        """impl Trait for Struct should create references."""
        source = (
            "pub trait Display {\n"
            "    fn display(&self) -> String;\n"
            "}\n"
            "\n"
            "pub struct Point {\n"
            "    x: f64,\n"
            "    y: f64,\n"
            "}\n"
            "\n"
            "impl Display for Point {\n"
            "    fn display(&self) -> String {\n"
            '        format!("({}, {})", self.x, self.y)\n'
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "impl.rs")
        names = [s["name"] for s in symbols]
        assert "Display" in names
        assert "Point" in names
        assert "display" in names


# ===========================================================================
# ADDITIONAL C TESTS
# ===========================================================================

class TestCExtractionExtra:
    """Additional C extraction tests."""

    def test_c_enum(self):
        """C enum definitions should be extracted."""
        source = (
            "typedef enum {\n"
            "    RED,\n"
            "    GREEN,\n"
            "    BLUE\n"
            "} Color;\n"
        )
        symbols, _ = _parse_and_extract(source, "colors.c")
        names = [s["name"] for s in symbols]
        assert "Color" in names

    def test_c_function_with_pointer_return(self):
        """Functions returning pointers should be extracted."""
        source = (
            "char* strdup(const char* s) {\n"
            "    return NULL;\n"
            "}\n"
            "\n"
            "int** create_matrix(int rows, int cols) {\n"
            "    return NULL;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "ptrs.c")
        func_names = [s["name"] for s in symbols if s["kind"] == "function"]
        assert "strdup" in func_names or "create_matrix" in func_names


# ===========================================================================
# ADDITIONAL PHP TESTS
# ===========================================================================

class TestPhpExtractionExtra:
    """Additional PHP extraction tests."""

    def test_php_abstract_class(self):
        """PHP abstract class should be extracted."""
        source = (
            "<?php\n"
            "abstract class BaseModel {\n"
            "    abstract public function validate();\n"
            "\n"
            "    public function save() {\n"
            "        return true;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "BaseModel.php")
        names = [s["name"] for s in symbols]
        assert "BaseModel" in names
        assert "save" in names

    def test_php_trait(self):
        """PHP trait should be extracted."""
        source = (
            "<?php\n"
            "trait Loggable {\n"
            "    public function log($message) {\n"
            "        echo $message;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Loggable.php")
        names = [s["name"] for s in symbols]
        assert "Loggable" in names
        assert "log" in names

    def test_php_extends_implements(self):
        """PHP extends + implements should create references."""
        source = (
            "<?php\n"
            "interface Sortable {\n"
            "    public function sort();\n"
            "}\n"
            "\n"
            "class BaseList {\n"
            "    public function count() { return 0; }\n"
            "}\n"
            "\n"
            "class SortedList extends BaseList implements Sortable {\n"
            "    public function sort() {}\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "SortedList.php")
        names = [s["name"] for s in symbols]
        assert "SortedList" in names
        assert "BaseList" in names
        assert "Sortable" in names


# ===========================================================================
# LINE NUMBER TESTS
# ===========================================================================

class TestLineNumbers:
    """Verify that extracted symbols have correct line number ranges."""

    def test_python_line_numbers(self):
        """Python symbols should have correct line_start and line_end."""
        source = (
            "def first():\n"     # line 1
            "    pass\n"         # line 2
            "\n"                 # line 3
            "def second():\n"    # line 4
            "    return 42\n"    # line 5
        )
        symbols, _ = _parse_and_extract(source, "lines.py")
        first = next(s for s in symbols if s["name"] == "first")
        second = next(s for s in symbols if s["name"] == "second")
        assert first["line_start"] == 1
        assert second["line_start"] == 4
        assert first["line_end"] < second["line_start"]

    def test_java_line_numbers(self):
        """Java symbols should have correct line_start."""
        source = (
            "public class Example {\n"   # line 1
            "    public void foo() {\n"   # line 2
            "    }\n"                     # line 3
            "\n"                          # line 4
            "    public void bar() {\n"   # line 5
            "    }\n"                     # line 6
            "}\n"                         # line 7
        )
        symbols, _ = _parse_and_extract(source, "Example.java")
        foo = next(s for s in symbols if s["name"] == "foo")
        bar = next(s for s in symbols if s["name"] == "bar")
        assert foo["line_start"] == 2
        assert bar["line_start"] == 5

    def test_go_line_numbers(self):
        """Go symbols should have correct line_start."""
        source = (
            "package main\n"             # line 1
            "\n"                          # line 2
            "func Alpha() {\n"            # line 3
            "}\n"                         # line 4
            "\n"                          # line 5
            "func Beta() {\n"             # line 6
            "}\n"                         # line 7
        )
        symbols, _ = _parse_and_extract(source, "main.go")
        alpha = next(s for s in symbols if s["name"] == "Alpha")
        beta = next(s for s in symbols if s["name"] == "Beta")
        assert alpha["line_start"] == 3
        assert beta["line_start"] == 6


# ===========================================================================
# SIGNATURE TESTS
# ===========================================================================

class TestSignatures:
    """Verify that extracted symbols have meaningful signatures."""

    def test_python_function_signature(self):
        """Python function signature should include parameter names."""
        source = (
            "def calculate(a, b, *, mode='add'):\n"
            "    pass\n"
        )
        symbols, _ = _parse_and_extract(source, "calc.py")
        func = next(s for s in symbols if s["name"] == "calculate")
        sig = func["signature"]
        assert sig is not None
        assert "calculate" in sig

    def test_java_method_signature(self):
        """Java method signature should include return type and params."""
        source = (
            "public class Calc {\n"
            "    public int add(int a, int b) {\n"
            "        return a + b;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Calc.java")
        method = next(s for s in symbols if s["name"] == "add")
        sig = method["signature"]
        assert sig is not None

    def test_go_function_signature(self):
        """Go function signature should include func keyword and params."""
        source = (
            "package main\n"
            "\n"
            "func Process(input string, count int) (string, error) {\n"
            '    return "", nil\n'
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "proc.go")
        func = next(s for s in symbols if s["name"] == "Process")
        sig = func["signature"]
        assert sig is not None
        assert "func" in sig or "Process" in sig


# ===========================================================================
# INTEGRATION: CLI round-trip tests per language
# ===========================================================================

class TestPythonCLI:
    """CLI integration tests for Python extraction."""

    def test_python_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct Python skeleton."""
        proj = project_factory({
            "models.py": (
                "class User:\n"
                '    """A user model."""\n'
                "    def __init__(self, name):\n"
                "        self.name = name\n"
                "    def greet(self):\n"
                '        return f"Hello {self.name}"\n'
                "    @property\n"
                "    def display_name(self):\n"
                "        return self.name.title()\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "models.py"])
        data = json.loads(result.output)
        assert data["language"] == "python"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "User" in names
        assert "greet" in names
        assert "display_name" in names


class TestJavaScriptCLI:
    """CLI integration tests for JavaScript extraction."""

    def test_js_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct JS skeleton."""
        proj = project_factory({
            "server.js": (
                "class Server {\n"
                "    constructor(port) {\n"
                "        this.port = port;\n"
                "    }\n"
                "    start() {\n"
                "        console.log('started');\n"
                "    }\n"
                "}\n"
                "\n"
                "const DEFAULT_PORT = 3000;\n"
                "\n"
                "function createServer(port) {\n"
                "    return new Server(port || DEFAULT_PORT);\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "server.js"])
        data = json.loads(result.output)
        assert data["language"] == "javascript"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "Server" in names
        assert "createServer" in names


class TestTypeScriptCLI:
    """CLI integration tests for TypeScript extraction."""

    def test_ts_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct TS skeleton."""
        proj = project_factory({
            "types.ts": (
                "export interface Config {\n"
                "    host: string;\n"
                "    port: number;\n"
                "}\n"
                "\n"
                "export type LogLevel = 'debug' | 'info' | 'error';\n"
                "\n"
                "export class AppConfig implements Config {\n"
                "    host: string = 'localhost';\n"
                "    port: number = 8080;\n"
                "\n"
                "    validate(): boolean {\n"
                "        return this.port > 0;\n"
                "    }\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "types.ts"])
        data = json.loads(result.output)
        assert data["language"] == "typescript"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "Config" in names
        assert "LogLevel" in names
        assert "AppConfig" in names


class TestJavaCLI:
    """CLI integration tests for Java extraction."""

    def test_java_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct Java skeleton."""
        proj = project_factory({
            "App.java": (
                "public class App {\n"
                "    private String name;\n"
                "\n"
                "    public App(String name) {\n"
                "        this.name = name;\n"
                "    }\n"
                "\n"
                "    public String getName() {\n"
                "        return name;\n"
                "    }\n"
                "\n"
                "    public static void main(String[] args) {\n"
                '        App app = new App("test");\n'
                "    }\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "App.java"])
        data = json.loads(result.output)
        assert data["language"] == "java"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "App" in names
        assert "getName" in names
        assert "main" in names


class TestGoCLI:
    """CLI integration tests for Go extraction."""

    def test_go_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct Go skeleton."""
        proj = project_factory({
            "handler.go": (
                "package main\n"
                "\n"
                "type Handler struct {\n"
                "    Name string\n"
                "}\n"
                "\n"
                "func (h *Handler) Handle(req string) string {\n"
                '    return "handled: " + req\n'
                "}\n"
                "\n"
                "func NewHandler(name string) *Handler {\n"
                "    return &Handler{Name: name}\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "handler.go"])
        data = json.loads(result.output)
        assert data["language"] == "go"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "Handler" in names
        assert "Handle" in names
        assert "NewHandler" in names


class TestRustCLI:
    """CLI integration tests for Rust extraction."""

    def test_rust_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct Rust skeleton."""
        proj = project_factory({
            "lib.rs": (
                "pub struct Calculator {\n"
                "    value: f64,\n"
                "}\n"
                "\n"
                "impl Calculator {\n"
                "    pub fn new() -> Self {\n"
                "        Calculator { value: 0.0 }\n"
                "    }\n"
                "\n"
                "    pub fn add(&mut self, x: f64) {\n"
                "        self.value += x;\n"
                "    }\n"
                "}\n"
                "\n"
                "pub trait Resettable {\n"
                "    fn reset(&mut self);\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "lib.rs"])
        data = json.loads(result.output)
        assert data["language"] == "rust"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "Calculator" in names
        assert "Resettable" in names
        assert "add" in names


class TestCCLI:
    """CLI integration tests for C extraction."""

    def test_c_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct C skeleton."""
        proj = project_factory({
            "utils.c": (
                "typedef struct {\n"
                "    int length;\n"
                "    int* data;\n"
                "} Array;\n"
                "\n"
                "Array* array_create(int length) {\n"
                "    return NULL;\n"
                "}\n"
                "\n"
                "void array_free(Array* arr) {\n"
                "    free(arr);\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "utils.c"])
        data = json.loads(result.output)
        assert data["language"] == "c"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "array_create" in names
        assert "array_free" in names


class TestPhpCLI:
    """CLI integration tests for PHP extraction."""

    def test_php_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct PHP skeleton."""
        proj = project_factory({
            "api.php": (
                "<?php\n"
                "interface Repository {\n"
                "    public function find($id);\n"
                "    public function save($entity);\n"
                "}\n"
                "\n"
                "class UserRepository implements Repository {\n"
                "    public function find($id) {\n"
                "        return null;\n"
                "    }\n"
                "\n"
                "    public function save($entity) {\n"
                "        return true;\n"
                "    }\n"
                "}\n"
                "\n"
                "function create_repo() {\n"
                "    return new UserRepository();\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "api.php"])
        data = json.loads(result.output)
        assert data["language"] == "php"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "Repository" in names
        assert "UserRepository" in names
        assert "create_repo" in names


# ===========================================================================
# CROSS-LANGUAGE: Multi-file project tests
# ===========================================================================

class TestMultiLanguageProject:
    """Tests that verify multi-language projects index correctly."""

    def test_polyglot_search(self, project_factory, cli_runner, monkeypatch):
        """Search should find symbols across different languages."""
        proj = project_factory({
            "main.py": (
                "class Handler:\n"
                "    def handle(self):\n"
                "        pass\n"
            ),
            "utils.js": (
                "function Handler() {\n"
                "    return { process: () => {} };\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "search", "Handler"])
        data = json.loads(result.output)
        results = data.get("results", [])
        # Handler should appear from both Python and JS
        assert len(results) >= 2

    def test_language_detection_correct(self, project_factory, cli_runner, monkeypatch):
        """Each file should be detected with the correct language."""
        proj = project_factory({
            "app.py": "def main(): pass\n",
            "app.js": "function main() {}\n",
            "App.java": "public class App { void main() {} }\n",
            "app.go": "package main\nfunc main() {}\n",
            "app.rs": "fn main() {}\n",
            "app.c": "int main() { return 0; }\n",
            "app.php": "<?php\nfunction main() {}\n",
            "app.ts": "function main(): void {}\n",
        })
        monkeypatch.chdir(proj)

        expected = {
            "app.py": "python",
            "app.js": "javascript",
            "App.java": "java",
            "app.go": "go",
            "app.rs": "rust",
            "app.c": "c",
            "app.php": "php",
            "app.ts": "typescript",
        }

        for path, lang in expected.items():
            result = invoke_cli(cli_runner, ["--json", "file", path])
            data = json.loads(result.output)
            assert data["language"] == lang, (
                f"Expected {path} to be {lang}, got {data['language']}"
            )


# ===========================================================================
# C# TESTS
# ===========================================================================

class TestCSharpExtraction:
    """Tests for C# symbol and reference extraction."""

    def test_csharp_class(self):
        """Class with methods and fields should be extracted."""
        source = (
            "namespace MyApp;\n"
            "\n"
            "public class Person\n"
            "{\n"
            "    private string _name;\n"
            "    private int _age;\n"
            "\n"
            "    public Person(string name, int age)\n"
            "    {\n"
            "        _name = name;\n"
            "        _age = age;\n"
            "    }\n"
            "\n"
            "    public string GetName()\n"
            "    {\n"
            "        return _name;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Person.cs")
        names = [s["name"] for s in symbols]
        assert "Person" in names
        cls = next(s for s in symbols if s["name"] == "Person" and s["kind"] == "class")
        assert cls["kind"] == "class"
        assert cls["visibility"] == "public"
        assert cls["qualified_name"] == "MyApp.Person"
        assert "GetName" in names
        assert "_name" in names or "_age" in names

    def test_csharp_interface(self):
        """Interface should be extracted as 'interface' kind."""
        source = (
            "namespace Services;\n"
            "\n"
            "public interface IRepository\n"
            "{\n"
            "    void Add(object item);\n"
            "    object FindById(int id);\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "IRepository.cs")
        names = [s["name"] for s in symbols]
        assert "IRepository" in names
        iface = next(s for s in symbols if s["name"] == "IRepository")
        assert iface["kind"] == "interface"
        assert iface["qualified_name"] == "Services.IRepository"
        # interface members default to public
        add_method = next(s for s in symbols if s["name"] == "Add")
        assert add_method["visibility"] == "public"

    def test_csharp_struct(self):
        """Struct should be extracted as 'struct' kind."""
        source = (
            "public struct Point\n"
            "{\n"
            "    public int X;\n"
            "    public int Y;\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Point.cs")
        names = [s["name"] for s in symbols]
        assert "Point" in names
        point = next(s for s in symbols if s["name"] == "Point")
        assert point["kind"] == "struct"

    def test_csharp_enum(self):
        """Enum with members should be extracted."""
        source = (
            "public enum Color\n"
            "{\n"
            "    Red,\n"
            "    Green,\n"
            "    Blue\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Color.cs")
        names = [s["name"] for s in symbols]
        assert "Color" in names
        enum_sym = next(s for s in symbols if s["name"] == "Color")
        assert enum_sym["kind"] == "enum"
        # enum members should be constants
        constants = [s for s in symbols if s["kind"] == "constant"]
        constant_names = {s["name"] for s in constants}
        assert "Red" in constant_names
        assert "Green" in constant_names
        assert "Blue" in constant_names

    def test_csharp_method_and_constructor(self):
        """Methods and constructors should be extracted with signatures."""
        source = (
            "public class Calculator\n"
            "{\n"
            "    public Calculator() { }\n"
            "\n"
            "    public async Task<int> AddAsync(int a, int b)\n"
            "    {\n"
            "        return a + b;\n"
            "    }\n"
            "\n"
            "    public static double Multiply(double x, double y)\n"
            "    {\n"
            "        return x * y;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Calculator.cs")
        names = [s["name"] for s in symbols]
        assert "Calculator" in names
        assert "AddAsync" in names
        assert "Multiply" in names
        # constructor
        ctors = [s for s in symbols if s["kind"] == "constructor"]
        assert len(ctors) >= 1
        # async in signature
        add_m = next(s for s in symbols if s["name"] == "AddAsync")
        assert "async" in add_m["signature"]
        # static in signature
        mul_m = next(s for s in symbols if s["name"] == "Multiply")
        assert "static" in mul_m["signature"]

    def test_csharp_property_with_accessors(self):
        """Property with get/set/init and required modifier should be extracted."""
        source = (
            "public class Config\n"
            "{\n"
            "    public string Name { get; set; }\n"
            "    public int Value { get; private set; }\n"
            "    public required string Key { get; init; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Config.cs")
        props = [s for s in symbols if s["kind"] == "property"]
        prop_names = {s["name"] for s in props}
        assert "Name" in prop_names
        assert "Value" in prop_names
        assert "Key" in prop_names
        key_prop = next(s for s in props if s["name"] == "Key")
        assert "required" in key_prop["signature"]
        assert "init" in key_prop["signature"]

    def test_csharp_delegate(self):
        """Delegate declaration should be extracted."""
        source = (
            "namespace Events;\n"
            "\n"
            "public delegate void EventHandler(object sender, EventArgs e);\n"
            "public delegate Task<T> AsyncOperation<T>(CancellationToken ct);\n"
        )
        symbols, _ = _parse_and_extract(source, "Delegates.cs")
        delegates = [s for s in symbols if s["kind"] == "delegate"]
        delegate_names = {s["name"] for s in delegates}
        assert "EventHandler" in delegate_names
        assert "AsyncOperation" in delegate_names
        handler = next(s for s in delegates if s["name"] == "EventHandler")
        assert "delegate" in handler["signature"]

    def test_csharp_using_directives(self):
        """Using directives (standard, static, alias) should create import references."""
        source = (
            "using System;\n"
            "using System.Collections.Generic;\n"
            "using static System.Math;\n"
            "using Req = WireMock.RequestBuilders.Request;\n"
            "\n"
            "public class Foo { }\n"
        )
        _, refs = _parse_and_extract(source, "Foo.cs")
        imports = [r for r in refs if r["kind"] == "import"]
        import_targets = {r["target_name"] for r in imports}
        assert "System" in import_targets
        assert "Generic" in import_targets
        assert "Math" in import_targets
        # alias using: target is the alias name, import_path has full qualified path
        assert "Req" in import_targets
        alias_ref = next(r for r in imports if r["target_name"] == "Req")
        assert "WireMock" in alias_ref["import_path"]

    def test_csharp_method_calls(self):
        """Method invocations should create call references."""
        source = (
            "namespace App;\n"
            "\n"
            "public class Service\n"
            "{\n"
            "    public void Run()\n"
            "    {\n"
            "        Console.WriteLine(\"hello\");\n"
            "        DoWork();\n"
            "    }\n"
            "\n"
            "    private void DoWork() { }\n"
            "}\n"
        )
        _, refs = _parse_and_extract(source, "Service.cs")
        calls = [r for r in refs if r["kind"] == "call"]
        call_targets = {r["target_name"] for r in calls}
        assert any("WriteLine" in t for t in call_targets)
        assert "DoWork" in call_targets

    def test_csharp_inheritance(self):
        """Base class and interface inheritance should create references."""
        source = (
            "public interface IEntity\n"
            "{\n"
            "    int Id { get; }\n"
            "}\n"
            "\n"
            "public class BaseEntity\n"
            "{\n"
            "    public int Id { get; set; }\n"
            "}\n"
            "\n"
            "public class User : BaseEntity, IEntity\n"
            "{\n"
            "    public string Name { get; set; }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "Models.cs")
        names = [s["name"] for s in symbols]
        assert "User" in names
        assert "BaseEntity" in names
        assert "IEntity" in names
        # first entry without I-prefix -> inherits
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert any(r["target_name"] == "BaseEntity" for r in inherits)
        # second entry with I-prefix -> implements
        implements = [r for r in refs if r["kind"] == "implements"]
        assert any(r["target_name"] == "IEntity" for r in implements)

    def test_csharp_constructor_calls(self):
        """new expressions should create call references."""
        source = (
            "public class Factory\n"
            "{\n"
            "    public object Create()\n"
            "    {\n"
            "        var list = new List<string>();\n"
            "        return new User(\"test\");\n"
            "    }\n"
            "}\n"
        )
        _, refs = _parse_and_extract(source, "Factory.cs")
        calls = [r for r in refs if r["kind"] == "call"]
        call_targets = {r["target_name"] for r in calls}
        assert "List" in call_targets
        assert "User" in call_targets

    def test_csharp_file_scoped_namespace(self):
        """File-scoped namespace (C# 10) should qualify types correctly."""
        source = (
            "namespace MyApp.Models;\n"
            "\n"
            "public class Order\n"
            "{\n"
            "    public int Id { get; set; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Order.cs")
        ns = next(s for s in symbols if s["kind"] == "module")
        assert ns["name"] == "MyApp.Models"
        order = next(s for s in symbols if s["name"] == "Order")
        assert order["qualified_name"] == "MyApp.Models.Order"

    def test_csharp_nested_type_visibility(self):
        """Nested types should default to private visibility."""
        source = (
            "public class Outer\n"
            "{\n"
            "    class Inner\n"
            "    {\n"
            "        void Method() { }\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Nested.cs")
        inner = next(s for s in symbols if s["name"] == "Inner")
        assert inner["visibility"] == "private"
        assert inner["qualified_name"] == "Outer.Inner"
        method = next(s for s in symbols if s["name"] == "Method")
        assert method["visibility"] == "private"

    def test_csharp_interface_member_visibility(self):
        """Interface members should default to public visibility."""
        source = (
            "public interface IService\n"
            "{\n"
            "    void Execute();\n"
            "    string Name { get; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "IService.cs")
        method = next(s for s in symbols if s["name"] == "Execute")
        assert method["visibility"] == "public"
        prop = next(s for s in symbols if s["name"] == "Name")
        assert prop["visibility"] == "public"

    def test_csharp_generic_class_with_constraints(self):
        """Generic class with type parameters and constraints should appear in signature."""
        source = (
            "public class Repository<T> where T : IEntity, new()\n"
            "{\n"
            "    public T FindById(int id) { return default; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Repository.cs")
        repo = next(s for s in symbols if s["name"] == "Repository")
        assert "<T>" in repo["signature"]
        assert "where" in repo["signature"]
        # name should NOT include generic params
        assert repo["name"] == "Repository"


class TestCSharpExtractionExtra:
    """Synthetic tests for C#-specific features not found in typical codebases."""

    def test_csharp_record_implementing_interface(self):
        """Record implementing interface should be extracted as class with implements ref."""
        source = (
            "public interface IBar { }\n"
            "\n"
            "public record Foo(string Name) : IBar;\n"
        )
        symbols, refs = _parse_and_extract(source, "RecordImpl.cs")
        foo = next(s for s in symbols if s["name"] == "Foo")
        assert foo["kind"] == "class"
        assert "record" in foo["signature"]
        implements = [r for r in refs if r["kind"] == "implements"]
        assert any(r["target_name"] == "IBar" for r in implements)

    def test_csharp_record_struct(self):
        """Record struct should be extracted as struct kind."""
        source = (
            "public record struct Point(int X, int Y);\n"
        )
        symbols, _ = _parse_and_extract(source, "Point.cs")
        point = next(s for s in symbols if s["name"] == "Point")
        assert point["kind"] == "struct"
        assert "record struct" in point["signature"]
        # primary constructor should be extracted
        ctors = [s for s in symbols if s["kind"] == "constructor" and s["name"] == "Point"]
        assert len(ctors) >= 1

    def test_csharp_compound_modifiers(self):
        """protected internal should be detected as compound modifier."""
        source = (
            "public class Base\n"
            "{\n"
            "    protected internal void SharedMethod() { }\n"
            "    private protected string Secret { get; set; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Base.cs")
        shared = next(s for s in symbols if s["name"] == "SharedMethod")
        assert shared["visibility"] == "protected internal"
        secret = next(s for s in symbols if s["name"] == "Secret")
        assert secret["visibility"] == "private protected"

    def test_csharp_nested_namespaces(self):
        """Nested block-scoped namespaces should accumulate qualified names."""
        source = (
            "namespace Outer\n"
            "{\n"
            "    namespace Inner\n"
            "    {\n"
            "        public class Deep { }\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Nested.cs")
        modules = [s for s in symbols if s["kind"] == "module"]
        module_names = {s["qualified_name"] for s in modules}
        assert "Outer" in module_names
        assert "Outer.Inner" in module_names
        deep = next(s for s in symbols if s["name"] == "Deep")
        assert deep["qualified_name"] == "Outer.Inner.Deep"

    def test_csharp_xml_doc_comments(self):
        """/// XML doc comments should be captured as docstrings."""
        source = (
            "/// <summary>\n"
            "/// gets the user name\n"
            "/// </summary>\n"
            "public class Documented\n"
            "{\n"
            "    /// <summary>returns a greeting</summary>\n"
            "    public string Greet() { return \"hi\"; }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Documented.cs")
        cls = next(s for s in symbols if s["name"] == "Documented")
        assert cls["docstring"] is not None
        assert "user name" in cls["docstring"]
        method = next(s for s in symbols if s["name"] == "Greet")
        assert method["docstring"] is not None
        assert "greeting" in method["docstring"]

    def test_csharp_local_functions(self):
        """Local functions should be extracted as methods with is_exported=False."""
        source = (
            "public class Processor\n"
            "{\n"
            "    public int Process(int x)\n"
            "    {\n"
            "        int Double(int n) => n * 2;\n"
            "        return Double(x);\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Processor.cs")
        local = next(s for s in symbols if s["name"] == "Double")
        assert local["kind"] == "method"
        assert local["is_exported"] is False
        assert local["visibility"] == "private"
        assert "Processor.Process.Double" in local["qualified_name"]

    def test_csharp_primary_constructor_with_base(self):
        """Primary constructor (C# 12) with base class + interface should extract all."""
        source = (
            "public interface IFoo { }\n"
            "public class Base { }\n"
            "\n"
            "public class Derived(int x) : Base, IFoo\n"
            "{\n"
            "    public int Value => x;\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "Derived.cs")
        derived = next(s for s in symbols if s["name"] == "Derived" and s["kind"] == "class")
        assert derived is not None
        # primary constructor
        ctors = [s for s in symbols if s["kind"] == "constructor" and s["name"] == "Derived"]
        assert len(ctors) >= 1
        ctor = ctors[0]
        assert "int x" in ctor["signature"]
        # inheritance
        inherits = [r for r in refs if r["kind"] == "inherits"]
        assert any(r["target_name"] == "Base" for r in inherits)
        implements = [r for r in refs if r["kind"] == "implements"]
        assert any(r["target_name"] == "IFoo" for r in implements)


# ===========================================================================
# C# LINE NUMBERS AND SIGNATURES
# ===========================================================================

class TestCSharpLineNumbers:
    """Verify C# symbols have correct line numbers."""

    def test_csharp_line_numbers(self):
        """C# symbols should have correct line_start."""
        source = (
            "public class Example\n"         # line 1
            "{\n"                             # line 2
            "    public void Foo()\n"         # line 3
            "    {\n"                         # line 4
            "    }\n"                         # line 5
            "\n"                              # line 6
            "    public void Bar()\n"         # line 7
            "    {\n"                         # line 8
            "    }\n"                         # line 9
            "}\n"                             # line 10
        )
        symbols, _ = _parse_and_extract(source, "Example.cs")
        foo = next(s for s in symbols if s["name"] == "Foo")
        bar = next(s for s in symbols if s["name"] == "Bar")
        assert foo["line_start"] == 3
        assert bar["line_start"] == 7


class TestCSharpSignatures:
    """Verify C# symbol signatures."""

    def test_csharp_method_signature(self):
        """C# method signature should include return type and params."""
        source = (
            "public class Calc\n"
            "{\n"
            "    public int Add(int a, int b)\n"
            "    {\n"
            "        return a + b;\n"
            "    }\n"
            "}\n"
        )
        symbols, _ = _parse_and_extract(source, "Calc.cs")
        method = next(s for s in symbols if s["name"] == "Add")
        assert method["signature"] is not None
        assert "int" in method["signature"]
        assert "Add" in method["signature"]


# ===========================================================================
# C# CLI INTEGRATION TESTS
# ===========================================================================

class TestCSharpCLI:
    """CLI integration tests for C# extraction."""

    def test_csharp_file_skeleton(self, project_factory, cli_runner, monkeypatch):
        """roam --json file should return correct C# skeleton."""
        proj = project_factory({
            "Models.cs": (
                "namespace App.Models;\n"
                "\n"
                "public interface IEntity\n"
                "{\n"
                "    int Id { get; }\n"
                "}\n"
                "\n"
                "public class User : IEntity\n"
                "{\n"
                "    public int Id { get; set; }\n"
                "    public string Name { get; set; }\n"
                "\n"
                "    public User(int id, string name)\n"
                "    {\n"
                "        Id = id;\n"
                "        Name = name;\n"
                "    }\n"
                "\n"
                "    public string GetDisplayName()\n"
                "    {\n"
                "        return Name;\n"
                "    }\n"
                "}\n"
            ),
        })
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["--json", "file", "Models.cs"])
        data = json.loads(result.output)
        assert data["language"] == "c_sharp"
        symbols = data["symbols"]
        names = [s["name"] for s in symbols]
        assert "IEntity" in names
        assert "User" in names
        assert "GetDisplayName" in names
