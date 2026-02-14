# Idea F: Pluggable Test Naming Conventions

## Origin
river-mounts fork -- Salesforce test naming (*Test.cls, *_Test.cls).
The idea is to support ALL ecosystems with a pluggable adapter system.

## Why
Current `_is_test_file()` uses two flat lists (duplicated between cmd_testmap.py
and changed_files.py). It misses: *Test.java, *Tests.java, *Test.kt, *Test.cs,
*Test.php, *Tests.swift, IT*.java, src/test/java/ directories, and more.

## Research Findings

### Convention Map (13 languages, 25+ frameworks)

| Language | Pattern | Directory |
|----------|---------|-----------|
| Python | test_*.py, *_test.py | tests/, test/ |
| JS/TS | *.test.js, *.spec.ts | __tests__/, test/ |
| Java | Test*.java, *Test.java, *Tests.java | src/test/java/ |
| Go | *_test.go | Same dir (mandatory) |
| Rust | inline #[cfg(test)], tests/*.rs | tests/ at crate root |
| C# | *Tests.cs, *Test.cs | Separate .Tests project |
| Ruby | *_spec.rb, test_*.rb | spec/, test/ |
| PHP | *Test.php | tests/ |
| Kotlin | *Test.kt | src/test/kotlin/ |
| Swift | *Tests.swift | Separate *Tests target |
| Scala | *Test.scala, *Spec.scala | src/test/scala/ |
| Elixir | *_test.exs | test/ |
| Dart | *_test.dart | test/ |
| Apex | *Test.cls, *_Test.cls | Already supported |

### Architecture: Three Layers

**Layer 1 -- TestConvention data class:**
```python
@dataclass
class TestConvention:
    framework: str
    file_patterns: list[str]    # basename globs
    dir_patterns: list[str]     # directory substrings
    co_located: bool
```

**Layer 2 -- Per-language adapters:**
```python
class TestConventionAdapter(ABC):
    def is_test_file(self, path: str) -> bool
    def find_test_candidates(self, source_path: str) -> list[str]
```

15 adapters: Python, JS/TS, Java, Go, Rust, C#, Ruby, PHP,
Kotlin, Swift, Scala, Elixir, Dart, Apex, Generic (fallback).

**Layer 3 -- Registry:**
```python
class TestConventionRegistry:
    def get_adapter(self, language) -> TestConventionAdapter
    def is_test_file(self, path, language=None) -> bool
    def find_test_candidates(self, source_path, language) -> list[str]
```

### Ambiguity Handling
1. Config file detection: pytest.ini, jest.config.js, etc. (ground truth)
2. Language-specific defaults if no config found
3. Frequency analysis: count matches per pattern, dominant wins
4. Per-directory override: different subdirs may use different frameworks

### Implementation Plan
1. Create `src/roam/index/test_conventions.py` with registry + adapters
2. Consolidate duplicated `_is_test_file()` from cmd_testmap.py + changed_files.py
3. Expose `find_test_candidates()` for source-to-test reverse mapping
4. Integrate into testmap, affected-tests, and file role classification

## Priority: MEDIUM (Tier 2)
## Effort: Medium
## Files touched: New test_conventions.py, cmd_testmap.py, changed_files.py
