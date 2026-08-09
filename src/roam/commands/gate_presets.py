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
    # Canonical two-tier bucket this rule gates on.
    # "error" blocks CI under `coverage-gaps --ci`; "warning" is advisory --
    # reported, never gated, in any mode. This is the COERCED value: any
    # ladder label ranking at or above `error` (critical / error / high)
    # lands in the "error" bucket. The label the user actually wrote is
    # preserved in ``severity_declared``.
    severity: str = "warning"
    # The severity label exactly as declared in .roam-gates.yml, before
    # coercion to the two-tier bucket. ``None`` for built-in preset rules,
    # which declare the canonical bucket directly. A user who writes
    # `severity: critical` must be able to see that roam read "critical" and
    # what it did with it -- publishing only the coerced value made a
    # demotion indistinguishable from an authored choice.
    severity_declared: str | None = None
    # True when ``severity_declared`` is not on the canonical ladder at all
    # (``severity_rank`` -> -1: a typo, or a word roam does not know like
    # "blocker"). Such a rule KEEPS the documented warning fallback per W531
    # -- a typo must never promote a finding into a CI-failing rank -- but the
    # coercion is now DISCLOSED rather than silent.
    severity_unrecognised: bool = False


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
                "tests/**",
                "test/**",
                "conftest.py",
                "setup.py",
                "**/migrations/**",
                "**/__init__.py",
                "**/conftest.py",
                "docs/**",
                "scripts/**",
                "examples/**",
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
                "**/*.test.*",
                "**/*.spec.*",
                "**/__tests__/**",
                "**/node_modules/**",
                "**/*.config.*",
                "**/*.d.ts",
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


def _blocking_rank() -> int:
    """Rank at or above which a gate rule blocks. Derived, never literal."""
    from roam.output._severity import severity_rank

    return severity_rank("error")


def coerce_gate_severity(raw: object) -> tuple[str, bool]:
    """Coerce a declared severity label to the gate's two-tier bucket.

    Returns ``(bucket, unrecognised)`` where ``bucket`` is ``"error"``
    (blocking) or ``"warning"`` (advisory), and ``unrecognised`` is True
    when the label is not on the canonical ladder at all.

    The vocabulary is NOT a local set. It is
    :func:`roam.output._severity.severity_rank`, the repo's single source of
    truth for severity ORDER, so the words a user may already have learned
    from `roam adversarial --min-severity` mean the same thing here. A rule
    blocks iff its declared label ranks at or above ``error`` -- which admits
    ``critical`` (5), ``error`` (4) and ``high`` (4), and nothing else.

    The previous implementation compared against a local two-element set,
    so ``severity: critical`` -- the HIGHEST tier on the ladder, strictly
    above ``error`` -- fell through the membership test and was rewritten to
    ``warning``, the least severe bucket, in silence. The user's most severe
    word disabled the gate it was written to arm.

    W531 is preserved exactly: a label that ranks -1 (a typo, or a word the
    ladder does not carry such as ``catastrophic`` / ``blocker``) still falls
    back to ``warning``. An unknown label must never promote a finding into a
    CI-failing rank. It is now reported as unrecognised so the fallback is
    visible instead of silent.
    """
    from roam.output._severity import severity_rank

    rank = severity_rank(raw if isinstance(raw, str) else None)
    if rank < 0:
        return "warning", True
    return ("error" if rank >= _blocking_rank() else "warning"), False


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

    ``severity`` accepts any label on the canonical roam ladder (see
    :func:`roam.output._severity.severity_rank`). A rule BLOCKS under
    ``coverage-gaps --ci`` iff its label ranks at or above ``error`` --
    that is ``critical``, ``error`` and ``high``. ``warning``, ``medium``,
    ``low`` and ``info`` are advisory. A label the ladder does not carry
    (a typo, or a word like ``blocker``) falls back to ``warning`` per W531
    and is reported through ``GateRule.severity_unrecognised`` so the
    coercion is disclosed rather than silent.

    W706 family: every fallback path is non-crashing. Missing PyYAML,
    missing file, malformed YAML, wrong top-level shape, and per-rule
    type errors all return ``[]`` rather than raise. Callers that need to
    distinguish "no rules" from "config broken" should ``Path.exists()``
    check first. Each fallback also emits an observability lineage signal
    (visible under ``ROAM_VERBOSE=1``) so a broken config isn't silently
    indistinguishable from an empty one. Encoding is pinned to UTF-8 so
    non-ASCII descriptions on Windows don't blow up on the system codepage.
    """
    try:
        import yaml
    except ImportError as _exc:
        # PyYAML absent -> no rules loadable. Surface the cause so a
        # missing optional dep isn't mistaken for "no rules configured".
        from roam.observability import log_swallowed

        log_swallowed("gate_presets:load_gates_config:pyyaml_missing", _exc)
        return []

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError) as _exc:
        # File missing / unreadable / bad encoding -> empty rule set.
        from roam.observability import log_swallowed

        log_swallowed("gate_presets:load_gates_config:file_read", _exc)
        return []
    except yaml.YAMLError as _exc:
        # Malformed YAML -> empty rule set. Surface the parse error so a
        # broken config isn't silently treated as "no rules".
        from roam.observability import log_swallowed

        log_swallowed("gate_presets:load_gates_config:yaml_parse", _exc)
        return []

    # W886/W1029 family: data may be None (empty YAML), a list, a scalar,
    # or any shape — only a mapping with a "rules" key is usable.
    if not isinstance(data, dict) or "rules" not in data:
        return []

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        return []

    rules: list[GateRule] = []
    for r in raw_rules:
        if not isinstance(r, dict):
            continue  # skip non-mapping entries (e.g. a bare string in the list)
        include = r.get("include", [])
        exclude = r.get("exclude", [])
        if not isinstance(include, list):
            include = []
        if not isinstance(exclude, list):
            exclude = []
        try:
            min_tests = int(r.get("min_tests", 1))
        except (TypeError, ValueError):
            # Expected: a non-numeric ``min_tests`` in user YAML falls
            # back to the documented default of 1 (per-rule, not fatal).
            min_tests = 1
        raw_severity = r.get("severity", "warning")
        severity, unrecognised = coerce_gate_severity(raw_severity)
        if unrecognised:
            # W531 keeps the warning fallback; this makes it AUDIBLE. The
            # three sibling signals in this function cover pyyaml-missing /
            # file-read / yaml-parse -- a rewritten severity emitted nothing
            # at all, so the one path that can silently disarm a gate was the
            # one path with no lineage record.
            from roam.observability import log_swallowed

            log_swallowed(
                "gate_presets:load_gates_config:severity_unrecognised",
                ValueError(
                    f"rule {r.get('name', 'unnamed')!r}: severity "
                    f"{raw_severity!r} is not a known severity - "
                    f"treated as 'warning' (advisory, never gates)"
                ),
            )
        rules.append(
            GateRule(
                name=str(r.get("name", "unnamed")),
                description=str(r.get("description", "")),
                include_patterns=[str(p) for p in include],
                exclude_patterns=[str(p) for p in exclude],
                min_test_count=min_tests,
                severity=severity,
                severity_declared=(raw_severity if isinstance(raw_severity, str) else str(raw_severity)),
                severity_unrecognised=unrecognised,
            )
        )
    return rules
