"""Framework gate presets for coverage-gaps policy enforcement.

Each preset defines:
- Which files must have test coverage
- What constitutes acceptable coverage (test file exists, test function count, etc.)
- Framework-specific conventions for test discovery
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateRule:
    """A single gate rule: which files must have tests."""
    name: str
    description: str
    # Glob patterns for files that MUST have tests
    include_patterns: list[str] = field(default_factory=list)
    # Glob patterns for files exempt from this rule
    exclude_patterns: list[str] = field(default_factory=list)
    # Minimum number of test functions expected
    min_test_count: int = 1
    # Severity: "error" (blocks CI) or "warning" (advisory)
    severity: str = "warning"


@dataclass
class GatePreset:
    """A collection of gate rules for a framework/language."""
    name: str
    description: str
    languages: list[str] = field(default_factory=list)
    rules: list[GateRule] = field(default_factory=list)
    # Files that auto-detect this preset
    detect_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

PRESET_PYTHON = GatePreset(
    name="python",
    description="Python project with pytest conventions",
    languages=["python"],
    detect_files=["pyproject.toml", "setup.py", "setup.cfg", "tox.ini"],
    rules=[
        GateRule(
            name="source-modules",
            description="All Python source modules should have test coverage",
            include_patterns=["src/**/*.py", "**/*.py"],
            exclude_patterns=[
                "tests/**", "test/**", "conftest.py", "setup.py",
                "**/migrations/**", "**/__init__.py", "**/conftest.py",
                "docs/**", "scripts/**", "examples/**",
            ],
            min_test_count=1,
            severity="warning",
        ),
        GateRule(
            name="critical-modules",
            description="Core business logic must have thorough tests",
            include_patterns=["src/**/models*.py", "src/**/service*.py", "src/**/api*.py"],
            exclude_patterns=["tests/**"],
            min_test_count=3,
            severity="error",
        ),
    ],
)

PRESET_JAVASCRIPT = GatePreset(
    name="javascript",
    description="JavaScript/TypeScript project with Jest/Vitest conventions",
    languages=["javascript", "typescript"],
    detect_files=["package.json", "tsconfig.json"],
    rules=[
        GateRule(
            name="source-modules",
            description="All JS/TS source modules should have test coverage",
            include_patterns=["src/**/*.{js,ts,jsx,tsx}"],
            exclude_patterns=[
                "**/*.test.*", "**/*.spec.*", "**/__tests__/**",
                "**/node_modules/**", "**/*.config.*", "**/*.d.ts",
            ],
            min_test_count=1,
            severity="warning",
        ),
    ],
)

PRESET_GO = GatePreset(
    name="go",
    description="Go project with colocated _test.go files",
    languages=["go"],
    detect_files=["go.mod", "go.sum"],
    rules=[
        GateRule(
            name="packages",
            description="All Go packages should have test files",
            include_patterns=["**/*.go"],
            exclude_patterns=["**/*_test.go", "vendor/**", "cmd/**"],
            min_test_count=1,
            severity="warning",
        ),
    ],
)

PRESET_JAVA = GatePreset(
    name="java-maven",
    description="Java Maven project with JUnit conventions",
    languages=["java"],
    detect_files=["pom.xml", "build.gradle", "build.gradle.kts"],
    rules=[
        GateRule(
            name="main-classes",
            description="Main source classes should have test counterparts",
            include_patterns=["src/main/**/*.java"],
            exclude_patterns=["**/dto/**", "**/entity/**", "**/config/**"],
            min_test_count=1,
            severity="warning",
        ),
    ],
)

PRESET_RUST = GatePreset(
    name="rust",
    description="Rust project with inline #[test] and tests/ directory",
    languages=["rust"],
    detect_files=["Cargo.toml"],
    rules=[
        GateRule(
            name="library-crates",
            description="Library source files should have tests",
            include_patterns=["src/**/*.rs"],
            exclude_patterns=["src/main.rs", "tests/**", "benches/**"],
            min_test_count=1,
            severity="warning",
        ),
    ],
)

ALL_PRESETS = [
    PRESET_PYTHON,
    PRESET_JAVASCRIPT,
    PRESET_GO,
    PRESET_JAVA,
    PRESET_RUST,
]


def get_preset(name: str) -> GatePreset | None:
    """Get a preset by name."""
    for p in ALL_PRESETS:
        if p.name == name:
            return p
    return None


def detect_preset(file_paths: list[str]) -> GatePreset | None:
    """Auto-detect the best preset for a project based on its files."""
    import os
    basenames = {os.path.basename(f) for f in file_paths}

    for preset in ALL_PRESETS:
        if any(df in basenames for df in preset.detect_files):
            return preset
    return None


def load_gates_config(config_path: str) -> list[GateRule]:
    """Load gate rules from a .roam-gates.yml file.

    Expected YAML format::

        rules:
          - name: critical-api
            description: API modules must have tests
            include: ["src/api/**/*.py"]
            exclude: ["**/__init__.py"]
            min_tests: 3
            severity: error
    """
    try:
        import yaml
    except ImportError:
        return []

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        return []

    rules = []
    for r in data["rules"]:
        rules.append(GateRule(
            name=r.get("name", "unnamed"),
            description=r.get("description", ""),
            include_patterns=r.get("include", []),
            exclude_patterns=r.get("exclude", []),
            min_test_count=r.get("min_tests", 1),
            severity=r.get("severity", "warning"),
        ))
    return rules
