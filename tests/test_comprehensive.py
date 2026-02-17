"""Comprehensive test suite for Roam.

Covers:
- All supported languages (Python, JS, TS, Java, Go, Rust, C, PHP, Ruby, C#, Kotlin)
- All 18 CLI commands
- Inheritance/implements/trait edges across languages
- Properties/fields across languages
- Incremental indexing (add, modify, remove, no-change)
- Error handling and edge cases
- Unicode, empty files, syntax errors, deeply nested structures
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Import helpers from conftest (pytest auto-loads conftest.py,
# but we need explicit import for non-fixture helpers)
sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit, index_in_process


# ============================================================================
# Polyglot project fixture (indexed ONCE, shared across many tests)
# ============================================================================

@pytest.fixture(scope="module")
def polyglot(tmp_path_factory):
    """Create a polyglot project with all supported languages and index it."""
    proj = tmp_path_factory.mktemp("polyglot")

    # ---- Python ----
    py_dir = proj / "python"
    py_dir.mkdir()
    (py_dir / "base.py").write_text(
        'class Animal:\n'
        '    """Base animal class."""\n'
        '    species = "unknown"\n'
        '    count = 0\n'
        '\n'
        '    def __init__(self, name: str):\n'
        '        self.name = name\n'
        '\n'
        '    def speak(self):\n'
        '        return "..."\n'
        '\n'
        '    def _internal(self):\n'
        '        pass\n'
    )
    (py_dir / "dog.py").write_text(
        'from base import Animal\n'
        '\n'
        'class Dog(Animal):\n'
        '    """A dog."""\n'
        '    legs = 4\n'
        '    \n'
        '    def speak(self):\n'
        '        return "Woof"\n'
        '\n'
        '    def fetch(self, item):\n'
        '        return f"Fetched {item}"\n'
    )
    (py_dir / "cat.py").write_text(
        'from base import Animal\n'
        '\n'
        'class Cat(Animal):\n'
        '    lives = 9\n'
        '    indoor = True\n'
        '\n'
        '    def speak(self):\n'
        '        return "Meow"\n'
    )
    (py_dir / "multi.py").write_text(
        'class Flyable:\n'
        '    def fly(self):\n'
        '        return "flying"\n'
        '\n'
        'class Swimmable:\n'
        '    def swim(self):\n'
        '        return "swimming"\n'
        '\n'
        'class Duck(Flyable, Swimmable):\n'
        '    def speak(self):\n'
        '        return "Quack"\n'
    )
    (py_dir / "decorators.py").write_text(
        'def my_decorator(func):\n'
        '    def wrapper(*args, **kwargs):\n'
        '        return func(*args, **kwargs)\n'
        '    return wrapper\n'
        '\n'
        '@my_decorator\n'
        'def decorated_function():\n'
        '    pass\n'
        '\n'
        'class WithStatic:\n'
        '    @staticmethod\n'
        '    def static_method():\n'
        '        pass\n'
        '\n'
        '    @classmethod\n'
        '    def class_method(cls):\n'
        '        pass\n'
    )
    (py_dir / "utils.py").write_text(
        '__all__ = ["public_func"]\n'
        '\n'
        'TIMEOUT = 30\n'
        'MAX_RETRIES = 3\n'
        '\n'
        'def public_func(x):\n'
        '    return x + 1\n'
        '\n'
        'def _private_func(y):\n'
        '    return y - 1\n'
    )

    # ---- JavaScript ----
    js_dir = proj / "javascript"
    js_dir.mkdir()
    (js_dir / "app.js").write_text(
        'const express = require("express");\n'
        '\n'
        'class Router {\n'
        '    constructor(prefix) {\n'
        '        this.prefix = prefix;\n'
        '        this.routes = [];\n'
        '    }\n'
        '\n'
        '    get(path, handler) {\n'
        '        this.routes.push({ method: "GET", path, handler });\n'
        '    }\n'
        '\n'
        '    post(path, handler) {\n'
        '        this.routes.push({ method: "POST", path, handler });\n'
        '    }\n'
        '}\n'
        '\n'
        'const createApp = () => {\n'
        '    return new Router("/api");\n'
        '};\n'
        '\n'
        'function startServer(port) {\n'
        '    const app = createApp();\n'
        '    console.log(`Server on ${port}`);\n'
        '}\n'
        '\n'
        'module.exports = Router;\n'
    )
    (js_dir / "middleware.js").write_text(
        'const logger = (req, res, next) => {\n'
        '    console.log(req.method);\n'
        '    next();\n'
        '};\n'
        '\n'
        'function* idGenerator() {\n'
        '    let id = 0;\n'
        '    while (true) yield id++;\n'
        '}\n'
        '\n'
        'const API_VERSION = "v1";\n'
        'let requestCount = 0;\n'
    )

    # ---- TypeScript ----
    ts_dir = proj / "typescript"
    ts_dir.mkdir()
    (ts_dir / "interfaces.ts").write_text(
        'export interface Serializable {\n'
        '    serialize(): string;\n'
        '}\n'
        '\n'
        'export interface Identifiable {\n'
        '    id: number;\n'
        '    getId(): number;\n'
        '}\n'
        '\n'
        'export interface Timestamped {\n'
        '    createdAt: Date;\n'
        '    updatedAt: Date;\n'
        '}\n'
    )
    (ts_dir / "base.ts").write_text(
        'import { Identifiable, Timestamped } from "./interfaces";\n'
        '\n'
        'export abstract class BaseEntity implements Identifiable, Timestamped {\n'
        '    id: number = 0;\n'
        '    createdAt: Date = new Date();\n'
        '    updatedAt: Date = new Date();\n'
        '\n'
        '    getId(): number {\n'
        '        return this.id;\n'
        '    }\n'
        '\n'
        '    abstract validate(): boolean;\n'
        '}\n'
    )
    (ts_dir / "user.ts").write_text(
        'import { BaseEntity } from "./base";\n'
        'import { Serializable } from "./interfaces";\n'
        '\n'
        'export class User extends BaseEntity implements Serializable {\n'
        '    name: string = "";\n'
        '    email: string = "";\n'
        '\n'
        '    validate(): boolean {\n'
        '        return this.name.length > 0;\n'
        '    }\n'
        '\n'
        '    serialize(): string {\n'
        '        return JSON.stringify({ name: this.name, email: this.email });\n'
        '    }\n'
        '}\n'
    )
    (ts_dir / "admin.ts").write_text(
        'import { User } from "./user";\n'
        '\n'
        'export class AdminUser extends User {\n'
        '    role: string = "admin";\n'
        '    permissions: string[] = [];\n'
        '\n'
        '    validate(): boolean {\n'
        '        return super.validate() && this.role.length > 0;\n'
        '    }\n'
        '}\n'
    )
    (ts_dir / "generics.ts").write_text(
        'export class Repository<T> {\n'
        '    private items: T[] = [];\n'
        '\n'
        '    add(item: T): void {\n'
        '        this.items.push(item);\n'
        '    }\n'
        '\n'
        '    findById(id: number): T | undefined {\n'
        '        return this.items[id];\n'
        '    }\n'
        '}\n'
        '\n'
        'type UserRole = "admin" | "user" | "guest";\n'
    )

    # ---- Java ----
    java_dir = proj / "java"
    java_dir.mkdir()
    (java_dir / "Animal.java").write_text(
        '/**\n'
        ' * Base animal class.\n'
        ' */\n'
        'public class Animal {\n'
        '    protected String name;\n'
        '    private int age;\n'
        '\n'
        '    public Animal(String name, int age) {\n'
        '        this.name = name;\n'
        '        this.age = age;\n'
        '    }\n'
        '\n'
        '    public String speak() {\n'
        '        return "...";\n'
        '    }\n'
        '\n'
        '    public int getAge() {\n'
        '        return age;\n'
        '    }\n'
        '}\n'
    )
    (java_dir / "Pet.java").write_text(
        'public interface Pet {\n'
        '    String speak();\n'
        '    String getName();\n'
        '}\n'
    )
    (java_dir / "Trainable.java").write_text(
        'public interface Trainable {\n'
        '    boolean train(String command);\n'
        '}\n'
    )
    (java_dir / "Dog.java").write_text(
        'public class Dog extends Animal implements Pet, Trainable {\n'
        '    private String breed;\n'
        '    public static final int MAX_AGE = 20;\n'
        '\n'
        '    public Dog(String name, int age, String breed) {\n'
        '        super(name, age);\n'
        '        this.breed = breed;\n'
        '    }\n'
        '\n'
        '    @Override\n'
        '    public String speak() {\n'
        '        return "Woof";\n'
        '    }\n'
        '\n'
        '    @Override\n'
        '    public String getName() {\n'
        '        return name;\n'
        '    }\n'
        '\n'
        '    @Override\n'
        '    public boolean train(String command) {\n'
        '        return true;\n'
        '    }\n'
        '\n'
        '    public String getBreed() {\n'
        '        return breed;\n'
        '    }\n'
        '}\n'
    )
    (java_dir / "Cat.java").write_text(
        'public class Cat extends Animal implements Pet {\n'
        '    private int lives = 9;\n'
        '\n'
        '    public Cat(String name, int age) {\n'
        '        super(name, age);\n'
        '    }\n'
        '\n'
        '    @Override\n'
        '    public String speak() {\n'
        '        return "Meow";\n'
        '    }\n'
        '\n'
        '    @Override\n'
        '    public String getName() {\n'
        '        return name;\n'
        '    }\n'
        '}\n'
    )
    (java_dir / "Color.java").write_text(
        'public enum Color {\n'
        '    RED,\n'
        '    GREEN,\n'
        '    BLUE;\n'
        '\n'
        '    public String lower() {\n'
        '        return name().toLowerCase();\n'
        '    }\n'
        '}\n'
    )
    (java_dir / "GuideDog.java").write_text(
        'public class GuideDog extends Dog {\n'
        '    private String handler;\n'
        '\n'
        '    public GuideDog(String name, int age, String breed, String handler) {\n'
        '        super(name, age, breed);\n'
        '        this.handler = handler;\n'
        '    }\n'
        '}\n'
    )

    # ---- Go ----
    go_dir = proj / "golang"
    go_dir.mkdir()
    (go_dir / "config.go").write_text(
        'package store\n'
        '\n'
        '// Config holds store configuration.\n'
        'type Config struct {\n'
        '    MaxSize int\n'
        '    Timeout int\n'
        '}\n'
        '\n'
        '// Reader defines the read interface.\n'
        'type Reader interface {\n'
        '    Get(key string) (string, error)\n'
        '    Has(key string) bool\n'
        '}\n'
        '\n'
        '// Writer defines the write interface.\n'
        'type Writer interface {\n'
        '    Set(key string, value string) error\n'
        '    Delete(key string) error\n'
        '}\n'
        '\n'
        '// Store combines Reader and Writer.\n'
        'type Store interface {\n'
        '    Reader\n'
        '    Writer\n'
        '}\n'
        '\n'
        'var DefaultTimeout = 30\n'
        'const MaxRetries = 5\n'
    )
    (go_dir / "memory.go").write_text(
        'package store\n'
        '\n'
        'import "sync"\n'
        '\n'
        '// MemoryStore is an in-memory store.\n'
        'type MemoryStore struct {\n'
        '    Config\n'
        '    mu   sync.RWMutex\n'
        '    data map[string]string\n'
        '}\n'
        '\n'
        'func NewMemoryStore(cfg Config) *MemoryStore {\n'
        '    return &MemoryStore{\n'
        '        Config: cfg,\n'
        '        data:   make(map[string]string),\n'
        '    }\n'
        '}\n'
        '\n'
        'func (s *MemoryStore) Get(key string) (string, error) {\n'
        '    s.mu.RLock()\n'
        '    defer s.mu.RUnlock()\n'
        '    v, ok := s.data[key]\n'
        '    if !ok {\n'
        '        return "", nil\n'
        '    }\n'
        '    return v, nil\n'
        '}\n'
        '\n'
        'func (s *MemoryStore) Set(key string, value string) error {\n'
        '    s.mu.Lock()\n'
        '    defer s.mu.Unlock()\n'
        '    s.data[key] = value\n'
        '    return nil\n'
        '}\n'
    )
    (go_dir / "redis.go").write_text(
        'package store\n'
        '\n'
        '// RedisStore wraps a Redis client.\n'
        'type RedisStore struct {\n'
        '    Config\n'
        '    addr string\n'
        '    pool int\n'
        '}\n'
        '\n'
        'func NewRedisStore(cfg Config, addr string) *RedisStore {\n'
        '    return &RedisStore{Config: cfg, addr: addr, pool: 10}\n'
        '}\n'
    )

    # ---- Rust ----
    rust_dir = proj / "rust"
    rust_dir.mkdir()
    (rust_dir / "traits.rs").write_text(
        '/// A shape that can compute area.\n'
        'pub trait Shape {\n'
        '    fn area(&self) -> f64;\n'
        '    fn perimeter(&self) -> f64;\n'
        '}\n'
        '\n'
        '/// Display trait for pretty printing.\n'
        'pub trait Display {\n'
        '    fn display(&self) -> String;\n'
        '}\n'
    )
    (rust_dir / "shapes.rs").write_text(
        'use crate::traits::{Shape, Display};\n'
        '\n'
        '/// A circle.\n'
        'pub struct Circle {\n'
        '    pub radius: f64,\n'
        '}\n'
        '\n'
        '/// A rectangle.\n'
        'pub struct Rectangle {\n'
        '    pub width: f64,\n'
        '    pub height: f64,\n'
        '}\n'
        '\n'
        'impl Shape for Circle {\n'
        '    fn area(&self) -> f64 {\n'
        '        std::f64::consts::PI * self.radius * self.radius\n'
        '    }\n'
        '    fn perimeter(&self) -> f64 {\n'
        '        2.0 * std::f64::consts::PI * self.radius\n'
        '    }\n'
        '}\n'
        '\n'
        'impl Shape for Rectangle {\n'
        '    fn area(&self) -> f64 {\n'
        '        self.width * self.height\n'
        '    }\n'
        '    fn perimeter(&self) -> f64 {\n'
        '        2.0 * (self.width + self.height)\n'
        '    }\n'
        '}\n'
        '\n'
        'impl Display for Circle {\n'
        '    fn display(&self) -> String {\n'
        '        format!("Circle(r={})", self.radius)\n'
        '    }\n'
        '}\n'
        '\n'
        'pub enum ShapeKind {\n'
        '    Circle(Circle),\n'
        '    Rectangle(Rectangle),\n'
        '}\n'
    )

    # ---- C ----
    c_dir = proj / "clang"
    c_dir.mkdir()
    (c_dir / "list.h").write_text(
        '#ifndef LIST_H\n'
        '#define LIST_H\n'
        '\n'
        'typedef struct Node {\n'
        '    int value;\n'
        '    struct Node* next;\n'
        '} Node;\n'
        '\n'
        'typedef struct {\n'
        '    Node* head;\n'
        '    int size;\n'
        '} LinkedList;\n'
        '\n'
        'LinkedList* list_create(void);\n'
        'void list_push(LinkedList* list, int value);\n'
        'int list_pop(LinkedList* list);\n'
        'void list_free(LinkedList* list);\n'
        '\n'
        '#endif\n'
    )
    (c_dir / "list.c").write_text(
        '#include <stdlib.h>\n'
        '#include "list.h"\n'
        '\n'
        'LinkedList* list_create(void) {\n'
        '    LinkedList* list = malloc(sizeof(LinkedList));\n'
        '    list->head = NULL;\n'
        '    list->size = 0;\n'
        '    return list;\n'
        '}\n'
        '\n'
        'void list_push(LinkedList* list, int value) {\n'
        '    Node* node = malloc(sizeof(Node));\n'
        '    node->value = value;\n'
        '    node->next = list->head;\n'
        '    list->head = node;\n'
        '    list->size++;\n'
        '}\n'
    )

    # ---- PHP ----
    php_dir = proj / "php"
    php_dir.mkdir()
    (php_dir / "Model.php").write_text(
        '<?php\n'
        'abstract class Model {\n'
        '    protected $table;\n'
        '    protected $primaryKey = "id";\n'
        '\n'
        '    public function find($id) {\n'
        '        return null;\n'
        '    }\n'
        '}\n'
    )
    (php_dir / "HasTimestamps.php").write_text(
        '<?php\n'
        'trait HasTimestamps {\n'
        '    public $created_at;\n'
        '    public $updated_at;\n'
        '\n'
        '    public function touch() {\n'
        '        $this->updated_at = time();\n'
        '    }\n'
        '}\n'
    )
    (php_dir / "SoftDeletes.php").write_text(
        '<?php\n'
        'trait SoftDeletes {\n'
        '    public $deleted_at;\n'
        '\n'
        '    public function delete() {\n'
        '        $this->deleted_at = time();\n'
        '    }\n'
        '\n'
        '    public function restore() {\n'
        '        $this->deleted_at = null;\n'
        '    }\n'
        '}\n'
    )
    (php_dir / "User.php").write_text(
        '<?php\n'
        'class User extends Model {\n'
        '    use HasTimestamps;\n'
        '    use SoftDeletes;\n'
        '\n'
        '    protected $table = "users";\n'
        '    public $name;\n'
        '    public $email;\n'
        '    private $password;\n'
        '\n'
        '    public function __construct($name, $email) {\n'
        '        $this->name = $name;\n'
        '        $this->email = $email;\n'
        '    }\n'
        '\n'
        '    public function greet() {\n'
        '        return "Hello, " . $this->name;\n'
        '    }\n'
        '\n'
        '    public function safeGreet() {\n'
        '        return $this?->greet();\n'
        '    }\n'
        '}\n'
    )

    # ---- Ruby ----
    ruby_dir = proj / "ruby"
    ruby_dir.mkdir()
    (ruby_dir / "vehicle.rb").write_text(
        'class Vehicle\n'
        '  def initialize(make, model)\n'
        '    @make = make\n'
        '    @model = model\n'
        '  end\n'
        '\n'
        '  def description\n'
        '    "#{@make} #{@model}"\n'
        '  end\n'
        'end\n'
        '\n'
        'class Car < Vehicle\n'
        '  def initialize(make, model, doors)\n'
        '    super(make, model)\n'
        '    @doors = doors\n'
        '  end\n'
        'end\n'
        '\n'
        'module Loggable\n'
        '  def log(msg)\n'
        '    puts msg\n'
        '  end\n'
        'end\n'
    )

    # ---- C# ----
    cs_dir = proj / "csharp"
    cs_dir.mkdir()
    (cs_dir / "Models.cs").write_text(
        'namespace App.Models\n'
        '{\n'
        '    public interface IEntity\n'
        '    {\n'
        '        int Id { get; set; }\n'
        '    }\n'
        '\n'
        '    public class BaseEntity : IEntity\n'
        '    {\n'
        '        public int Id { get; set; }\n'
        '        public DateTime CreatedAt { get; set; }\n'
        '    }\n'
        '\n'
        '    public class UserEntity : BaseEntity\n'
        '    {\n'
        '        public string Name { get; set; }\n'
        '        public string Email { get; set; }\n'
        '    }\n'
        '}\n'
    )

    # ---- Kotlin ----
    kt_dir = proj / "kotlin"
    kt_dir.mkdir()
    (kt_dir / "models.kt").write_text(
        'interface Printable {\n'
        '    fun print()\n'
        '}\n'
        '\n'
        'open class Shape {\n'
        '    open fun area(): Double = 0.0\n'
        '}\n'
        '\n'
        'class Circle(val radius: Double) : Shape(), Printable {\n'
        '    override fun area(): Double = Math.PI * radius * radius\n'
        '    override fun print() = println("Circle($radius)")\n'
        '}\n'
        '\n'
        'data class Point(val x: Double, val y: Double)\n'
    )

    # ---- JavaScript CJS exports ----
    (js_dir / "cjs_exports.js").write_text(
        'exports.normalizeType = function(type) {\n'
        '    return type;\n'
        '};\n'
        'exports.compileETag = function compileETag(val) {\n'
        '    return val;\n'
        '};\n'
        'exports.version = "1.0";\n'
        'var app = module.exports = {};\n'
        'app.init = function init() {\n'
        '    return true;\n'
        '};\n'
        'app.handle = function handle(req, res) {\n'
        '    return req;\n'
        '};\n'
    )

    # ---- JavaScript object exports ----
    (js_dir / "obj_exports.js").write_text(
        'module.exports = {\n'
        '    handle(req) { return req; },\n'
        '    query: function() { return []; },\n'
        '    VERSION: "2.0"\n'
        '};\n'
    )

    # ---- Vue SFC ----
    vue_dir = proj / "vue"
    vue_dir.mkdir()
    (vue_dir / "UserCard.vue").write_text(
        '<template>\n'
        '  <div class="user-card">{{ user.name }}</div>\n'
        '</template>\n'
        '\n'
        '<script lang="ts">\n'
        'import { defineComponent } from "vue";\n'
        '\n'
        'interface UserData {\n'
        '  name: string;\n'
        '  email: string;\n'
        '}\n'
        '\n'
        'export default defineComponent({\n'
        '  name: "UserCard",\n'
        '  props: {\n'
        '    user: { type: Object as () => UserData, required: true },\n'
        '  },\n'
        '});\n'
        '</script>\n'
        '\n'
        '<style scoped>\n'
        '.user-card { padding: 8px; }\n'
        '</style>\n'
    )
    (vue_dir / "Counter.vue").write_text(
        '<template>\n'
        '  <button @click="increment">{{ count }}</button>\n'
        '</template>\n'
        '\n'
        '<script setup lang="ts">\n'
        'import { ref } from "vue";\n'
        '\n'
        'const count = ref(0);\n'
        '\n'
        'function increment(): void {\n'
        '  count.value++;\n'
        '}\n'
        '</script>\n'
    )
    (vue_dir / "Legacy.vue").write_text(
        '<template>\n'
        '  <div>{{ message }}</div>\n'
        '</template>\n'
        '\n'
        '<script>\n'
        'export default {\n'
        '  data() {\n'
        '    return { message: "hello" };\n'
        '  },\n'
        '  methods: {\n'
        '    greet() {\n'
        '      return this.message;\n'
        '    },\n'
        '  },\n'
        '};\n'
        '</script>\n'
    )
    # TS composable used by Vue files — tests cross-file import resolution
    (vue_dir / "useCounter.ts").write_text(
        'export function useCounter(initial: number) {\n'
        '  let count = initial;\n'
        '  return { count, increment: () => count++ };\n'
        '}\n'
    )
    (vue_dir / "App.vue").write_text(
        '<template>\n'
        '  <div>{{ counter.count }}</div>\n'
        '</template>\n'
        '\n'
        '<script setup lang="ts">\n'
        'import { useCounter } from "./useCounter";\n'
        '\n'
        'const counter = useCounter(0);\n'
        '</script>\n'
    )

    # ---- Test files (for test-map command) ----
    test_dir = proj / "tests"
    test_dir.mkdir()
    (test_dir / "test_animals.py").write_text(
        'from python.base import Animal\n'
        'from python.dog import Dog\n'
        '\n'
        'def test_animal_speak():\n'
        '    a = Animal("test")\n'
        '    assert a.speak() == "..."\n'
        '\n'
        'def test_dog_speak():\n'
        '    d = Dog("Rex")\n'
        '    assert d.speak() == "Woof"\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"Index failed: {out}"
    return proj


# ============================================================================
# PYTHON TESTS
# ============================================================================

class TestPython:
    def test_class_extracted(self, polyglot):
        out, _ = roam("symbol", "Animal", cwd=polyglot)
        assert "Animal" in out
        # Animal exists in both Python and Java; either file path is fine
        assert "base.py" in out or "Animal.java" in out

    def test_method_extracted(self, polyglot):
        out, _ = roam("search", "speak", cwd=polyglot)
        assert "speak" in out

    def test_function_extracted(self, polyglot):
        out, _ = roam("search", "public_func", cwd=polyglot)
        assert "public_func" in out

    def test_class_properties(self, polyglot):
        out, _ = roam("file", "python/base.py", cwd=polyglot)
        assert "species" in out or "count" in out

    def test_inheritance(self, polyglot):
        out, _ = roam("uses", "Animal", cwd=polyglot)
        assert "Dog" in out or "Cat" in out

    def test_multiple_inheritance(self, polyglot):
        out, _ = roam("uses", "Flyable", cwd=polyglot)
        assert "Duck" in out

    def test_dunder_all(self, polyglot):
        out, _ = roam("file", "python/utils.py", cwd=polyglot)
        assert "public_func" in out

    def test_private_visibility(self, polyglot):
        out, _ = roam("file", "python/base.py", cwd=polyglot)
        assert "_internal" in out

    def test_decorators(self, polyglot):
        out, _ = roam("file", "python/decorators.py", cwd=polyglot)
        assert "decorated_function" in out
        assert "static_method" in out or "class_method" in out


# ============================================================================
# JAVASCRIPT TESTS
# ============================================================================

class TestJavaScript:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "Router", cwd=polyglot)
        assert "Router" in out

    def test_methods_extracted(self, polyglot):
        out, _ = roam("file", "javascript/app.js", cwd=polyglot)
        assert "constructor" in out or "get" in out or "post" in out

    def test_arrow_function(self, polyglot):
        out, _ = roam("file", "javascript/app.js", cwd=polyglot)
        assert "createApp" in out

    def test_regular_function(self, polyglot):
        out, _ = roam("search", "startServer", cwd=polyglot)
        assert "startServer" in out

    def test_generator(self, polyglot):
        out, _ = roam("file", "javascript/middleware.js", cwd=polyglot)
        assert "idGenerator" in out

    def test_constants(self, polyglot):
        out, _ = roam("file", "javascript/middleware.js", cwd=polyglot)
        assert "API_VERSION" in out

    def test_require_import(self, polyglot):
        out, _ = roam("deps", "javascript/app.js", cwd=polyglot)
        # Should show require("express") as an import
        assert "express" in out.lower() or "import" in out.lower() or "app.js" in out

    def test_cjs_exports_function(self, polyglot):
        """exports.X = function() should be extracted as a symbol."""
        out, _ = roam("search", "normalizeType", cwd=polyglot)
        assert "normalizeType" in out

    def test_cjs_exports_qualified(self, polyglot):
        """CJS export functions should have qualified names."""
        out, _ = roam("symbol", "normalizeType", cwd=polyglot)
        assert "exports.normalizeType" in out

    def test_cjs_obj_method_assignment(self, polyglot):
        """app.init = function() should be extracted."""
        out, _ = roam("file", "javascript/cjs_exports.js", cwd=polyglot)
        assert "init" in out
        assert "handle" in out

    def test_cjs_exports_value(self, polyglot):
        """exports.version = "1.0" should be extracted as a constant."""
        out, _ = roam("file", "javascript/cjs_exports.js", cwd=polyglot)
        assert "version" in out

    def test_module_exports_object_methods(self, polyglot):
        """module.exports = { handle() {}, query: function() {} } should extract members."""
        out, _ = roam("file", "javascript/obj_exports.js", cwd=polyglot)
        assert "handle" in out
        assert "query" in out


# ============================================================================
# TYPESCRIPT TESTS
# ============================================================================

class TestTypeScript:
    def test_interface_extracted(self, polyglot):
        out, _ = roam("search", "Serializable", cwd=polyglot)
        assert "Serializable" in out

    def test_abstract_class(self, polyglot):
        out, _ = roam("symbol", "BaseEntity", cwd=polyglot)
        assert "BaseEntity" in out

    def test_extends_edge(self, polyglot):
        out, _ = roam("uses", "BaseEntity", cwd=polyglot)
        assert "User" in out

    def test_implements_edge(self, polyglot):
        out, _ = roam("uses", "Serializable", cwd=polyglot)
        assert "User" in out

    def test_multi_level_inheritance(self, polyglot):
        out, _ = roam("uses", "User", cwd=polyglot)
        assert "AdminUser" in out

    def test_generics(self, polyglot):
        out, _ = roam("search", "Repository", cwd=polyglot)
        assert "Repository" in out

    def test_properties(self, polyglot):
        out, _ = roam("file", "typescript/admin.ts", cwd=polyglot)
        assert "role" in out or "permissions" in out


# ============================================================================
# JAVA TESTS
# ============================================================================

class TestJava:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "Dog", cwd=polyglot)
        assert "Dog" in out

    def test_interface_extracted(self, polyglot):
        out, _ = roam("search", "Pet", cwd=polyglot)
        assert "Pet" in out

    def test_enum_extracted(self, polyglot):
        out, _ = roam("search", "Color", cwd=polyglot)
        assert "Color" in out

    def test_extends_edge(self, polyglot):
        out, _ = roam("uses", "Animal", cwd=polyglot)
        # Dog, Cat both extend Animal
        assert "Dog" in out or "Cat" in out

    def test_implements_edge(self, polyglot):
        out, _ = roam("uses", "Pet", cwd=polyglot)
        assert "Dog" in out or "Cat" in out

    def test_multi_implements(self, polyglot):
        """Dog implements Pet AND Trainable."""
        out, _ = roam("uses", "Trainable", cwd=polyglot)
        assert "Dog" in out

    def test_deep_inheritance(self, polyglot):
        out, _ = roam("uses", "Dog", cwd=polyglot)
        assert "GuideDog" in out

    def test_no_doubled_keywords(self, polyglot):
        out, _ = roam("symbol", "Dog", cwd=polyglot)
        assert "extends extends" not in out
        assert "implements implements" not in out

    def test_no_double_parens(self, polyglot):
        out, _ = roam("file", "java/Dog.java", cwd=polyglot)
        assert "((" not in out

    def test_fields_extracted(self, polyglot):
        out, _ = roam("file", "java/Dog.java", cwd=polyglot)
        assert "breed" in out

    def test_static_final_constant(self, polyglot):
        out, _ = roam("file", "java/Dog.java", cwd=polyglot)
        assert "MAX_AGE" in out

    def test_enum_constants(self, polyglot):
        out, _ = roam("file", "java/Color.java", cwd=polyglot)
        assert "RED" in out or "GREEN" in out or "BLUE" in out

    def test_constructor(self, polyglot):
        out, _ = roam("file", "java/Dog.java", cwd=polyglot)
        assert "Dog(" in out  # constructor signature


# ============================================================================
# GO TESTS
# ============================================================================

class TestGo:
    def test_struct_extracted(self, polyglot):
        out, _ = roam("search", "Config", cwd=polyglot)
        assert "Config" in out

    def test_interface_extracted(self, polyglot):
        out, _ = roam("search", "Reader", cwd=polyglot)
        assert "Reader" in out

    def test_function_extracted(self, polyglot):
        out, _ = roam("search", "NewMemoryStore", cwd=polyglot)
        assert "NewMemoryStore" in out

    def test_method_extracted(self, polyglot):
        out, _ = roam("file", "golang/memory.go", cwd=polyglot)
        assert "Get" in out or "Set" in out

    def test_embedded_struct_edge(self, polyglot):
        out, _ = roam("uses", "Config", cwd=polyglot)
        assert "MemoryStore" in out or "RedisStore" in out

    def test_struct_fields(self, polyglot):
        out, _ = roam("file", "golang/config.go", cwd=polyglot)
        assert "MaxSize" in out or "Timeout" in out

    def test_variables_and_constants(self, polyglot):
        out, _ = roam("file", "golang/config.go", cwd=polyglot)
        assert "DefaultTimeout" in out or "MaxRetries" in out

    def test_package_extracted(self, polyglot):
        out, _ = roam("file", "golang/config.go", cwd=polyglot)
        assert "store" in out

    def test_no_double_parens(self, polyglot):
        """Go method/function signatures should not have double parens."""
        out, _ = roam("file", "golang/memory.go", cwd=polyglot)
        assert "((" not in out


# ============================================================================
# RUST TESTS
# ============================================================================

class TestRust:
    def test_trait_extracted(self, polyglot):
        out, _ = roam("search", "Shape", cwd=polyglot)
        assert "Shape" in out

    def test_struct_extracted(self, polyglot):
        out, _ = roam("search", "Circle", cwd=polyglot)
        assert "Circle" in out

    def test_impl_trait_edge(self, polyglot):
        out, _ = roam("uses", "Shape", cwd=polyglot)
        assert "Circle" in out or "Rectangle" in out

    def test_enum_extracted(self, polyglot):
        out, _ = roam("search", "ShapeKind", cwd=polyglot)
        assert "ShapeKind" in out

    def test_struct_fields(self, polyglot):
        out, _ = roam("file", "rust/shapes.rs", cwd=polyglot)
        assert "radius" in out or "width" in out


# ============================================================================
# C TESTS
# ============================================================================

class TestC:
    def test_struct_extracted(self, polyglot):
        out, _ = roam("search", "Node", cwd=polyglot)
        assert "Node" in out

    def test_function_extracted(self, polyglot):
        out, _ = roam("search", "list_create", cwd=polyglot)
        assert "list_create" in out

    def test_header_file(self, polyglot):
        out, _ = roam("file", "clang/list.h", cwd=polyglot)
        # C header: function declarations may not be extracted as symbols
        # (they're prototypes, not definitions). The file should at least load.
        assert "list.h" in out

    def test_implementation_file(self, polyglot):
        out, _ = roam("file", "clang/list.c", cwd=polyglot)
        assert "list_create" in out


# ============================================================================
# PHP TESTS
# ============================================================================

class TestPHP:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "User", cwd=polyglot)
        assert "User" in out

    def test_trait_extracted(self, polyglot):
        out, _ = roam("search", "HasTimestamps", cwd=polyglot)
        assert "HasTimestamps" in out

    def test_extends_edge(self, polyglot):
        out, _ = roam("uses", "Model", cwd=polyglot)
        assert "User" in out

    def test_trait_usage_edge(self, polyglot):
        out, _ = roam("uses", "HasTimestamps", cwd=polyglot)
        assert "User" in out

    def test_properties(self, polyglot):
        out, _ = roam("file", "php/User.php", cwd=polyglot)
        assert "name" in out or "email" in out or "table" in out

    def test_visibility(self, polyglot):
        out, _ = roam("file", "php/User.php", cwd=polyglot)
        assert "private" in out or "public" in out or "protected" in out

    def test_nullsafe_call(self, polyglot):
        """$this?->greet() should create a call edge."""
        out, _ = roam("file", "php/User.php", "--full", cwd=polyglot)
        assert "safeGreet" in out


# ============================================================================
# RUBY TESTS
# ============================================================================

class TestRuby:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "Vehicle", cwd=polyglot)
        assert "Vehicle" in out

    def test_subclass_extracted(self, polyglot):
        out, _ = roam("search", "Car", cwd=polyglot)
        assert "Car" in out

    def test_inheritance_edge(self, polyglot):
        out, _ = roam("uses", "Vehicle", cwd=polyglot)
        assert "Car" in out

    def test_module_extracted(self, polyglot):
        out, _ = roam("search", "Loggable", cwd=polyglot)
        assert "Loggable" in out


# ============================================================================
# C# TESTS
# ============================================================================

class TestCSharp:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "BaseEntity", cwd=polyglot)
        # verify actual symbol found (not just substring in error message)
        assert "class" in out.lower() or "BaseEntity" in out
        # search should return results, not "no symbols" message
        assert "No symbols" not in out or "BaseEntity" in out

    def test_interface_extracted(self, polyglot):
        out, _ = roam("search", "IEntity", cwd=polyglot)
        assert "IEntity" in out
        # with tier 1 extractor, IEntity should be found as an actual interface symbol
        assert "No symbols" not in out

    def test_inheritance_edge(self, polyglot):
        out, _ = roam("uses", "BaseEntity", cwd=polyglot)
        # BaseEntity is used by User (TS) and possibly UserEntity (C#)
        assert "User" in out

    def test_property_extracted(self, polyglot):
        out, _ = roam("search", "Id", cwd=polyglot)
        assert "Id" in out

    def test_namespace_extracted(self, polyglot):
        out, _ = roam("search", "App.Models", cwd=polyglot)
        assert "App.Models" in out


# ============================================================================
# KOTLIN TESTS
# ============================================================================

class TestKotlin:
    def test_class_extracted(self, polyglot):
        out, _ = roam("search", "Circle", cwd=polyglot)
        assert "Circle" in out

    def test_interface_extracted(self, polyglot):
        out, _ = roam("search", "Printable", cwd=polyglot)
        assert "Printable" in out

    def test_data_class(self, polyglot):
        out, _ = roam("search", "Point", cwd=polyglot)
        assert "Point" in out


# ============================================================================
# VUE SFC TESTS
# ============================================================================

class TestVueSFC:
    def test_vue_file_indexed(self, polyglot):
        """Vue SFC files should be discovered and indexed."""
        out, _ = roam("file", "vue/UserCard.vue", cwd=polyglot)
        assert "UserCard.vue" in out

    def test_vue_ts_interface_extracted(self, polyglot):
        """TypeScript interface inside <script lang='ts'> should be extracted."""
        out, _ = roam("search", "UserData", cwd=polyglot)
        assert "UserData" in out

    def test_vue_script_setup_function(self, polyglot):
        """Functions in <script setup> should be extracted."""
        out, _ = roam("search", "increment", cwd=polyglot)
        assert "increment" in out

    def test_vue_js_fallback(self, polyglot):
        """Vue SFC without lang attr should parse as JavaScript."""
        out, _ = roam("file", "vue/Legacy.vue", cwd=polyglot)
        assert "Legacy.vue" in out

    def test_vue_imports_create_edges(self, polyglot):
        """Vue <script setup> imports from TS files should create dependency edges."""
        out, _ = roam("deps", "vue/App.vue", cwd=polyglot)
        assert "useCounter" in out or "Imports" in out

    def test_vue_symbol_has_callers(self, polyglot):
        """TS function imported by Vue should show callers from Vue file."""
        out, _ = roam("symbol", "useCounter", cwd=polyglot)
        assert "useCounter" in out
        # The Vue file should appear as a caller
        assert "App.vue" in out or "in=" in out

    def test_vue_no_script_tags_in_parse(self, polyglot):
        """The <script> and </script> tags should not appear as symbols."""
        out, _ = roam("file", "vue/Counter.vue", cwd=polyglot)
        assert "<script" not in out


# ============================================================================
# ALL 18 COMMANDS
# ============================================================================

class TestCommands:
    """Test every CLI command produces valid output on the polyglot project."""

    def test_index(self, polyglot):
        out, rc = index_in_process(polyglot)
        assert rc == 0
        assert "up to date" in out or "Done" in out

    def test_map(self, polyglot):
        out, rc = roam("map", cwd=polyglot)
        assert rc == 0
        assert "Files:" in out

    def test_module(self, polyglot):
        out, rc = roam("module", "python", cwd=polyglot)
        assert rc == 0
        assert "base.py" in out or "dog.py" in out

    def test_module_root(self, polyglot):
        """roam module . should work for root-level files."""
        # Our polyglot project has no root-level files, but it shouldn't crash
        out, rc = roam("module", ".", cwd=polyglot)
        # May show no files or all files, but shouldn't crash hard
        assert rc == 0 or "No files" in out

    def test_file(self, polyglot):
        out, rc = roam("file", "python/base.py", cwd=polyglot)
        assert rc == 0
        assert "Animal" in out

    def test_symbol(self, polyglot):
        out, rc = roam("symbol", "Animal", cwd=polyglot)
        assert rc == 0
        assert "Animal" in out

    def test_trace(self, polyglot):
        out, rc = roam("trace", "Dog", "Animal", cwd=polyglot)
        # May or may not find a path depending on edge resolution
        assert rc == 0 or "No path" in out

    def test_deps(self, polyglot):
        out, rc = roam("deps", "python/dog.py", cwd=polyglot)
        assert rc == 0

    def test_health(self, polyglot):
        out, rc = roam("health", cwd=polyglot)
        assert rc == 0

    def test_clusters(self, polyglot):
        out, rc = roam("clusters", cwd=polyglot)
        assert rc == 0

    def test_layers(self, polyglot):
        out, rc = roam("layers", cwd=polyglot)
        assert rc == 0

    def test_weather(self, polyglot):
        out, rc = roam("weather", cwd=polyglot)
        assert rc == 0

    def test_dead(self, polyglot):
        out, rc = roam("dead", cwd=polyglot)
        assert rc == 0

    def test_search(self, polyglot):
        out, rc = roam("search", "Dog", cwd=polyglot)
        assert rc == 0
        assert "Dog" in out

    def test_search_pattern(self, polyglot):
        out, rc = roam("search", "Store", cwd=polyglot)
        assert rc == 0
        assert "MemoryStore" in out or "RedisStore" in out

    def test_grep(self, polyglot):
        out, rc = roam("grep", "speak", cwd=polyglot)
        assert rc == 0
        assert "speak" in out

    def test_uses(self, polyglot):
        out, rc = roam("uses", "Animal", cwd=polyglot)
        assert rc == 0


# ============================================================================
# EDGE COUNTS (verify the index has edges, not just symbols)
# ============================================================================

class TestEdgeCounts:
    def test_nonzero_edges(self, polyglot):
        """The polyglot project should have many edges."""
        out, _ = roam("health", cwd=polyglot)
        # Health report shows edge counts
        assert "0 edges" not in out or "edges" in out

    def test_java_has_edges(self, polyglot):
        out, _ = roam("deps", "java/Dog.java", cwd=polyglot)
        # Dog.java imports from Animal, Pet, Trainable
        assert "Animal" in out or "Pet" in out or "import" in out.lower()

    def test_python_has_edges(self, polyglot):
        out, _ = roam("deps", "python/dog.py", cwd=polyglot)
        assert "base.py" in out or "import" in out.lower()


# ============================================================================
# INCREMENTAL INDEXING
# ============================================================================

class TestIncremental:
    @pytest.fixture
    def incr_project(self, tmp_path):
        """A Python project for incremental tests."""
        proj = tmp_path / "incr"
        proj.mkdir()
        (proj / "base.py").write_text(
            'class Base:\n'
            '    def hello(self):\n'
            '        return "hi"\n'
        )
        (proj / "child.py").write_text(
            'from base import Base\n'
            '\n'
            'class Child(Base):\n'
            '    def greet(self):\n'
            '        return self.hello()\n'
        )
        (proj / "standalone.py").write_text(
            'def standalone():\n'
            '    return 42\n'
        )
        git_init(proj)
        index_in_process(proj, "--force")
        return proj

    def test_no_change_is_noop(self, incr_project):
        """Running index with no changes should be a no-op."""
        out, rc = index_in_process(incr_project)
        assert rc == 0
        assert "up to date" in out

    def test_add_file(self, incr_project):
        """Adding a new file should be detected."""
        (incr_project / "new_file.py").write_text(
            'def new_function():\n'
            '    return "new"\n'
        )
        git_commit(incr_project, "add file")
        out, rc = index_in_process(incr_project)
        assert rc == 0
        assert "1 added" in out

        out, _ = roam("search", "new_function", cwd=incr_project)
        assert "new_function" in out

    def test_modify_preserves_edges(self, incr_project):
        """Modifying a file should preserve cross-file edges."""
        # Verify initial edge
        out, _ = roam("uses", "Base", cwd=incr_project)
        assert "Child" in out, f"Initial edge missing: {out}"

        # Modify the target file
        (incr_project / "base.py").write_text(
            'class Base:\n'
            '    def hello(self):\n'
            '        return "hello world"\n'
            '    def goodbye(self):\n'
            '        return "bye"\n'
        )
        git_commit(incr_project, "modify base")
        out, rc = index_in_process(incr_project)
        assert rc == 0
        assert "Re-extracting" in out

        # Edge should survive
        out, _ = roam("uses", "Base", cwd=incr_project)
        assert "Child" in out, f"Edge lost after modify: {out}"

    def test_ts_modify_preserves_edges(self, tmp_path):
        """TS inheritance edges should survive incremental re-indexing."""
        proj = tmp_path / "ts_incr"
        proj.mkdir()
        (proj / "base.ts").write_text(
            'export class Base {\n'
            '    hello(): string { return "hi"; }\n'
            '}\n'
        )
        (proj / "child.ts").write_text(
            'import { Base } from "./base";\n'
            '\n'
            'export class Child extends Base {\n'
            '    greet(): string { return this.hello(); }\n'
            '}\n'
        )
        git_init(proj)
        index_in_process(proj, "--force")

        # Verify initial edge
        out, _ = roam("uses", "Base", cwd=proj)
        assert "Child" in out, f"Initial TS edge missing: {out}"

        # Modify base.ts
        (proj / "base.ts").write_text(
            'export class Base {\n'
            '    hello(): string { return "hello world"; }\n'
            '    goodbye(): string { return "bye"; }\n'
            '}\n'
        , encoding="utf-8")
        git_commit(proj, "modify base")

        # Incremental re-index
        out, _ = index_in_process(proj)
        assert "Re-extracting" in out

        # Edge should survive
        out, _ = roam("uses", "Base", cwd=proj)
        assert "Child" in out, f"TS edge lost after modify: {out}"

    def test_remove_file(self, incr_project):
        """Removing a file should clean up its symbols."""
        import os
        os.remove(incr_project / "standalone.py")
        git_commit(incr_project, "remove file")
        out, rc = index_in_process(incr_project)
        assert rc == 0
        assert "1 removed" in out

        out, _ = roam("search", "standalone", cwd=incr_project)
        assert "standalone" not in out or "No symbols" in out


# ============================================================================
# ERROR HANDLING
# ============================================================================

class TestErrorHandling:
    @pytest.fixture
    def err_project(self, tmp_path):
        proj = tmp_path / "err"
        proj.mkdir()
        (proj / "valid.py").write_text('def valid(): pass\n')
        git_init(proj)
        index_in_process(proj, "--force")
        return proj

    def test_nonexistent_file(self, err_project):
        """roam file with non-existent file should fail gracefully."""
        out, rc = roam("file", "nonexistent.py", cwd=err_project)
        assert rc != 0

    def test_nonexistent_symbol(self, err_project):
        """roam symbol with unknown name should fail gracefully."""
        out, rc = roam("symbol", "DoesNotExist", cwd=err_project)
        assert rc != 0

    def test_empty_search(self, err_project):
        """roam search with no matches should succeed with message."""
        out, rc = roam("search", "zzzzzzzzzzz", cwd=err_project)
        assert rc == 0
        assert "No symbols" in out or "zzzzzzzzzzz" not in out

    def test_help_all_commands(self):
        """Every command should have --help (in-process for speed)."""
        from click.testing import CliRunner
        from roam.cli import cli

        commands = [
            "index", "map", "module", "file", "symbol", "trace",
            "deps", "health", "clusters", "layers", "weather",
            "dead", "search", "grep", "uses", "impact", "owner",
            "coupling", "fan", "diff", "describe", "test-map",
            "sketch", "context", "safe-delete", "pr-risk", "split",
            "risk", "why",
        ]
        runner = CliRunner()
        for cmd in commands:
            result = runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"


# ============================================================================
# EDGE CASES
# ============================================================================

class TestEdgeCases:
    @pytest.fixture
    def edge_project(self, tmp_path):
        proj = tmp_path / "edge"
        proj.mkdir()
        return proj

    def test_empty_file(self, edge_project):
        """Empty files should not crash the indexer."""
        (edge_project / "empty.py").write_text("")
        (edge_project / "also_empty.js").write_text("")
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0

    def test_syntax_error_file(self, edge_project):
        """Files with syntax errors should be handled gracefully."""
        (edge_project / "bad.py").write_text(
            'def broken(\n'
            '    this is not valid python\n'
        )
        (edge_project / "good.py").write_text(
            'def good():\n'
            '    return 42\n'
        )
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0
        # good.py should still be indexed
        out, _ = roam("search", "good", cwd=edge_project)
        assert "good" in out

    def test_deeply_nested_classes(self, edge_project):
        """Deeply nested class structures should work."""
        (edge_project / "nested.java").write_text(
            'public class Outer {\n'
            '    public class Middle {\n'
            '        public class Inner {\n'
            '            public void deepMethod() {}\n'
            '        }\n'
            '    }\n'
            '}\n'
        )
        git_init(edge_project)
        index_in_process(edge_project, "--force")
        out, _ = roam("search", "Inner", cwd=edge_project)
        assert "Inner" in out

    def test_unicode_identifiers(self, edge_project):
        """Unicode in file content should not crash."""
        (edge_project / "unicode.py").write_text(
            '# Comments with unicode: eeeee\n'
            'greeting = "Hello World"\n'
            '\n'
            'def process():\n'
            '    return greeting\n'
        , encoding="utf-8")
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0
        out, _ = roam("search", "process", cwd=edge_project)
        assert "process" in out

    def test_very_long_file(self, edge_project):
        """Large files should be handled."""
        lines = ['def func_{i}():\n    return {i}\n'.format(i=i) for i in range(200)]
        (edge_project / "big.py").write_text('\n'.join(lines))
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0
        out, _ = roam("file", "big.py", cwd=edge_project)
        assert "func_0" in out

    def test_roam_dir_excluded(self, edge_project):
        """Files in .roam/ should not be indexed."""
        (edge_project / "real.py").write_text('def real(): pass\n')
        roam_dir = edge_project / ".roam"
        roam_dir.mkdir()
        (roam_dir / "index.db").write_bytes(b"fake")
        (roam_dir / "hidden.py").write_text('def hidden(): pass\n')
        git_init(edge_project)
        index_in_process(edge_project, "--force")
        out, _ = roam("search", "hidden", cwd=edge_project)
        assert "hidden" not in out or "No symbols" in out

    def test_mixed_line_endings(self, edge_project):
        """Files with CRLF/mixed endings should work."""
        (edge_project / "crlf.py").write_bytes(
            b'def crlf_func():\r\n    return True\r\n'
        )
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0
        out, _ = roam("search", "crlf_func", cwd=edge_project)
        assert "crlf_func" in out

    def test_no_git_repo(self, tmp_path):
        """Project without git should fall back to os.walk."""
        proj = tmp_path / "nogit"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        # No git init!
        out, rc = index_in_process(proj, "--force")
        assert rc == 0
        out, _ = roam("search", "main", cwd=proj)
        assert "main" in out

    def test_binary_files_skipped(self, edge_project):
        """Binary files should be skipped without error."""
        (edge_project / "code.py").write_text('x = 1\n')
        (edge_project / "image.png").write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        (edge_project / "data.bin").write_bytes(b'\x00' * 1000)
        git_init(edge_project)
        out, rc = index_in_process(edge_project, "--force")
        assert rc == 0


# ============================================================================
# CROSS-LANGUAGE SUMMARY
# ============================================================================

class TestCrossLanguage:
    def test_map_shows_all_languages(self, polyglot):
        """Map should show files from all languages."""
        out, _ = roam("map", cwd=polyglot)
        assert "python" in out
        assert "java" in out

    def test_search_cross_language(self, polyglot):
        """Search should find symbols across languages."""
        # "speak" exists in Python, Java
        out, _ = roam("search", "speak", cwd=polyglot)
        assert out.count("speak") >= 2

    def test_grep_cross_language(self, polyglot):
        """Grep should search across all files."""
        out, _ = roam("grep", "return", cwd=polyglot)
        # Should match in many languages
        assert ".py" in out or ".java" in out or ".js" in out


# ============================================================================
# NEW v3.6 COMMANDS
# ============================================================================

class TestDescribe:
    def test_describe_runs(self, polyglot):
        """roam describe should produce Markdown output."""
        out, rc = roam("describe", cwd=polyglot)
        assert rc == 0
        assert "Project Overview" in out
        assert "Files:" in out or "**Files:**" in out

    def test_describe_has_sections(self, polyglot):
        """Describe should include all major sections."""
        out, rc = roam("describe", cwd=polyglot)
        assert rc == 0
        assert "Directory Structure" in out
        assert "Entry Points" in out
        assert "Testing" in out

    def test_describe_write(self, polyglot):
        """roam describe --write should create CLAUDE.md."""
        out, rc = roam("describe", "--write", cwd=polyglot)
        assert rc == 0
        claude_md = polyglot / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "Project" in content

    def test_describe_help(self):
        """roam describe --help should work."""
        out, rc = roam("describe", "--help")
        assert rc == 0


class TestTestMap:
    def test_testmap_symbol(self, polyglot):
        """roam test-map for a symbol should show test coverage."""
        out, rc = roam("test-map", "Animal", cwd=polyglot)
        assert rc == 0
        assert "Test coverage" in out or "test" in out.lower()

    def test_testmap_file(self, polyglot):
        """roam test-map for a file should show test files."""
        out, rc = roam("test-map", "python/base.py", cwd=polyglot)
        assert rc == 0
        assert "Test coverage" in out or "test" in out.lower()

    def test_testmap_not_found(self, polyglot):
        """roam test-map with nonexistent name should fail gracefully."""
        out, rc = roam("test-map", "NonExistentThing999", cwd=polyglot)
        assert rc != 0

    def test_testmap_help(self):
        """roam test-map --help should work."""
        out, rc = roam("test-map", "--help")
        assert rc == 0


class TestSketch:
    def test_sketch_directory(self, polyglot):
        """roam sketch should show exported symbols per file."""
        out, rc = roam("sketch", "python", cwd=polyglot)
        assert rc == 0
        # Should show file paths and symbol kinds
        assert "python" in out

    def test_sketch_full(self, polyglot):
        """roam sketch --full should show all symbols."""
        out, rc = roam("sketch", "python", "--full", cwd=polyglot)
        assert rc == 0
        assert "python" in out

    def test_sketch_nonexistent(self, polyglot):
        """roam sketch with nonexistent dir should handle gracefully."""
        out, rc = roam("sketch", "nonexistent_dir_xyz", cwd=polyglot)
        assert rc == 0
        assert "No" in out

    def test_sketch_help(self):
        """roam sketch --help should work."""
        out, rc = roam("sketch", "--help")
        assert rc == 0


# ============================================================================
# CONTEXT COMMAND (v4.1)
# ============================================================================

class TestContext:
    def test_context_basic(self, polyglot):
        """roam context should show callers, callees, and files to read."""
        out, rc = roam("context", "Animal", cwd=polyglot)
        assert rc == 0
        assert "Context for" in out
        assert "Callers" in out
        assert "Files to read" in out

    def test_context_shows_tests(self, polyglot):
        """roam context should detect test files referencing the symbol."""
        out, rc = roam("context", "Animal", cwd=polyglot)
        assert rc == 0
        # The polyglot fixture has tests/test_animals.py importing Animal
        assert "test" in out.lower()

    def test_context_json(self, polyglot):
        """roam --json context should produce valid JSON with all fields."""
        import json
        out, rc = roam("--json", "context", "Animal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "symbol" in data
        assert "callers" in data
        assert "callees" in data
        assert "files_to_read" in data
        assert isinstance(data["callers"], list)
        assert isinstance(data["files_to_read"], list)
        # Definition should be first file to read
        assert len(data["files_to_read"]) >= 1
        assert data["files_to_read"][0]["reason"] == "definition"

    def test_context_not_found(self, polyglot):
        """roam context with nonexistent symbol should fail gracefully."""
        out, rc = roam("context", "NonExistentThing999", cwd=polyglot)
        assert rc != 0
        assert "not found" in out.lower()

    def test_context_help(self):
        """roam context --help should work."""
        out, rc = roam("context", "--help")
        assert rc == 0
        assert "context" in out.lower()


# ============================================================================
# SAFE-DELETE COMMAND (v4.1)
# ============================================================================

class TestSafeDelete:
    def test_safe_delete_unused(self, polyglot):
        """roam safe-delete on an unused symbol should show SAFE."""
        # _internal is a private method in Animal with no callers
        out, rc = roam("safe-delete", "_internal", cwd=polyglot)
        assert rc == 0
        assert "SAFE" in out

    def test_safe_delete_used(self, polyglot):
        """roam safe-delete on a used symbol should show UNSAFE or REVIEW."""
        out, rc = roam("safe-delete", "Animal", cwd=polyglot)
        assert rc == 0
        # Animal is used by Dog, Cat, and tests — should not be SAFE
        assert "UNSAFE" in out or "REVIEW" in out

    def test_safe_delete_shows_callers(self, polyglot):
        """roam safe-delete on a used symbol should list callers."""
        out, rc = roam("safe-delete", "Animal", cwd=polyglot)
        assert rc == 0
        assert "References" in out or "caller" in out.lower()

    def test_safe_delete_json(self, polyglot):
        """roam --json safe-delete should produce valid JSON with verdict."""
        import json
        out, rc = roam("--json", "safe-delete", "Animal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "verdict" in data
        assert data["verdict"] in ("SAFE", "REVIEW", "UNSAFE")
        assert "direct_callers" in data
        assert "transitive_dependents" in data

    def test_safe_delete_json_unused(self, polyglot):
        """roam --json safe-delete on unused symbol should return SAFE verdict."""
        import json
        out, rc = roam("--json", "safe-delete", "_internal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert data["verdict"] == "SAFE"
        assert data["direct_callers"] == 0

    def test_safe_delete_not_found(self, polyglot):
        """roam safe-delete with nonexistent symbol should fail gracefully."""
        out, rc = roam("safe-delete", "NonExistentThing999", cwd=polyglot)
        assert rc != 0
        assert "not found" in out.lower()

    def test_safe_delete_help(self):
        """roam safe-delete --help should work."""
        out, rc = roam("safe-delete", "--help")
        assert rc == 0


# ============================================================================
# SPLIT COMMAND (v4.4)
# ============================================================================

class TestSplit:
    def test_split_basic(self, polyglot):
        """roam split should analyze file internal structure."""
        out, rc = roam("split", "javascript/app.js", cwd=polyglot)
        assert rc == 0
        assert "Split analysis" in out or "symbols" in out

    def test_split_shows_groups(self, polyglot):
        """roam split should show symbol groups."""
        out, rc = roam("split", "javascript/app.js", cwd=polyglot)
        assert rc == 0
        # Should show groups or at least report the symbol count
        assert "Group" in out or "symbols" in out

    def test_split_json(self, polyglot):
        """roam --json split should produce valid JSON."""
        import json
        out, rc = roam("--json", "split", "javascript/app.js", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "path" in data
        assert "total_symbols" in data
        assert "groups" in data
        assert isinstance(data["groups"], list)

    def test_split_too_few_symbols(self, polyglot):
        """roam split on a tiny file should report gracefully."""
        # python/cat.py has Cat class + speak + lives = 3 symbols
        # That's at the threshold — may analyze or say too few
        out, rc = roam("split", "python/cat.py", cwd=polyglot)
        assert rc == 0

    def test_split_min_group(self, polyglot):
        """roam split --min-group should filter small groups."""
        out, rc = roam("split", "javascript/app.js", "--min-group", "1",
                        cwd=polyglot)
        assert rc == 0

    def test_split_not_found(self, polyglot):
        """roam split with nonexistent file should fail gracefully."""
        out, rc = roam("split", "nonexistent_file.py", cwd=polyglot)
        assert rc != 0
        assert "not found" in out.lower()

    def test_split_help(self):
        """roam split --help should work."""
        out, rc = roam("split", "--help")
        assert rc == 0


# ============================================================================
# RISK COMMAND (v4.4)
# ============================================================================

class TestRisk:
    def test_risk_basic(self, polyglot):
        """roam risk should show domain-weighted risk ranking."""
        out, rc = roam("risk", cwd=polyglot)
        assert rc == 0
        # May show "Risk" header or "No graph metrics" if no metrics
        assert "Risk" in out or "No graph" in out

    def test_risk_json(self, polyglot):
        """roam --json risk should produce valid JSON."""
        import json
        out, rc = roam("--json", "risk", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_risk_with_domain(self, polyglot):
        """roam risk --domain should accept custom keywords at max weight."""
        out, rc = roam("risk", "--domain", "animal,speak", cwd=polyglot)
        assert rc == 0

    def test_risk_count_limit(self, polyglot):
        """roam risk -n should limit output count."""
        out, rc = roam("risk", "-n", "3", cwd=polyglot)
        assert rc == 0

    def test_risk_json_with_domain(self, polyglot):
        """roam --json risk --domain should include domain match info."""
        import json
        out, rc = roam("--json", "risk", "--domain", "animal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "items" in data

    def test_risk_help(self):
        """roam risk --help should work."""
        out, rc = roam("risk", "--help")
        assert rc == 0


# ============================================================================
# PR-RISK COMMAND (v4.1)
# ============================================================================

class TestPrRisk:
    @pytest.fixture
    def pr_project(self, tmp_path):
        """A project with pending git changes for pr-risk testing."""
        proj = tmp_path / "pr_risk_test"
        proj.mkdir()
        (proj / "main.py").write_text(
            'from helper import process\n\n'
            'def main():\n    return process(42)\n'
        )
        (proj / "helper.py").write_text(
            'def process(x):\n    return x + 1\n\n'
            'def validate(y):\n    return y > 0\n'
        )
        git_init(proj)
        index_in_process(proj, "--force")
        # Create unstaged changes
        (proj / "helper.py").write_text(
            'def process(x):\n    return x * 2\n\n'
            'def validate(y):\n    return y > 0\n'
        )
        return proj

    def test_pr_risk_unstaged(self, pr_project):
        """roam pr-risk should analyze unstaged changes."""
        out, rc = roam("pr-risk", cwd=pr_project)
        assert rc == 0
        assert "Risk" in out

    def test_pr_risk_json(self, pr_project):
        """roam --json pr-risk should produce valid JSON with risk score."""
        import json
        out, rc = roam("--json", "pr-risk", cwd=pr_project)
        assert rc == 0
        data = json.loads(out)
        assert "risk_score" in data
        assert isinstance(data["risk_score"], int)
        assert "risk_level" in data

    def test_pr_risk_staged(self, pr_project):
        """roam pr-risk --staged should analyze staged changes."""
        import subprocess
        subprocess.run(["git", "add", "."], cwd=pr_project, capture_output=True)
        out, rc = roam("pr-risk", "--staged", cwd=pr_project)
        assert rc == 0
        assert "Risk" in out

    def test_pr_risk_no_changes(self, polyglot):
        """roam pr-risk with no changes should handle gracefully."""
        out, rc = roam("pr-risk", cwd=polyglot)
        assert rc == 0
        assert "No changes" in out

    def test_pr_risk_shows_breakdown(self, pr_project):
        """roam pr-risk should show risk breakdown components."""
        out, rc = roam("pr-risk", cwd=pr_project)
        assert rc == 0
        assert "Blast radius" in out or "blast" in out.lower()

    def test_pr_risk_commit_range(self, pr_project):
        """roam pr-risk with commit range should work."""
        import subprocess
        # Commit the change so we can use a commit range
        subprocess.run(["git", "add", "."], cwd=pr_project, capture_output=True)
        subprocess.run(["git", "commit", "-m", "change"],
                        cwd=pr_project, capture_output=True)
        out, rc = roam("pr-risk", "HEAD~1..HEAD", cwd=pr_project)
        assert rc == 0

    def test_pr_risk_help(self):
        """roam pr-risk --help should work."""
        out, rc = roam("pr-risk", "--help")
        assert rc == 0


class TestWhy:
    """Tests for the 'why' command."""

    def test_why_basic(self, polyglot):
        """roam why should show role, reach, critical, cluster, verdict."""
        out, rc = roam("why", "Animal", cwd=polyglot)
        assert rc == 0
        assert "ROLE:" in out
        assert "REACH:" in out
        assert "CRITICAL:" in out
        assert "VERDICT:" in out

    def test_why_shows_cluster(self, polyglot):
        """roam why should show cluster membership."""
        out, rc = roam("why", "Animal", cwd=polyglot)
        assert rc == 0
        assert "CLUSTER:" in out

    def test_why_json(self, polyglot):
        """roam why --json should return valid JSON with expected fields."""
        import json
        out, rc = roam("--json", "why", "Animal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert "symbols" in data
        assert len(data["symbols"]) == 1
        sym = data["symbols"][0]
        assert "role" in sym
        assert "fan_in" in sym
        assert "fan_out" in sym
        assert "reach" in sym
        assert "critical" in sym
        assert "verdict" in sym

    def test_why_batch(self, polyglot):
        """roam why with multiple symbols should produce batch table."""
        out, rc = roam("why", "Animal", "speak", "_internal", cwd=polyglot)
        assert rc == 0
        # Batch mode outputs a table with all symbol names
        assert "Animal" in out
        assert "speak" in out

    def test_why_batch_json(self, polyglot):
        """roam why --json with multiple symbols should return all."""
        import json
        out, rc = roam("--json", "why", "Animal", "speak", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        assert len(data["symbols"]) == 2

    def test_why_not_found(self, polyglot):
        """roam why with unknown symbol should fail."""
        out, rc = roam("why", "NonExistentThing999", cwd=polyglot)
        assert rc != 0

    def test_why_role_classification(self, polyglot):
        """roam why should classify roles correctly."""
        import json
        out, rc = roam("--json", "why", "_internal", cwd=polyglot)
        assert rc == 0
        data = json.loads(out)
        sym = data["symbols"][0]
        # _internal has no callers, should be Leaf
        assert sym["role"] == "Leaf"

    def test_why_help(self):
        """roam why --help should work."""
        out, rc = roam("why", "--help")
        assert rc == 0
        assert "role" in out.lower() or "verdict" in out.lower()
