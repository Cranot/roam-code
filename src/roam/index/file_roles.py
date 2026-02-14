"""Smart file role classifier using three-tier heuristics.

Classifies files into one of: source, test, config, build, docs,
generated, vendored, data, examples, scripts, ci.

Tier 1: Path-based (compiled regex, ~90% coverage, no I/O)
Tier 2: Filename + extension (no I/O)
Tier 3: Content-based (selective I/O, only for ambiguous files)
"""

from __future__ import annotations

import os.path
import re


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLE_SOURCE = "source"
ROLE_TEST = "test"
ROLE_CONFIG = "config"
ROLE_BUILD = "build"
ROLE_DOCS = "docs"
ROLE_GENERATED = "generated"
ROLE_VENDORED = "vendored"
ROLE_DATA = "data"
ROLE_EXAMPLES = "examples"
ROLE_SCRIPTS = "scripts"
ROLE_CI = "ci"

ALL_ROLES = frozenset({
    ROLE_SOURCE, ROLE_TEST, ROLE_CONFIG, ROLE_BUILD, ROLE_DOCS,
    ROLE_GENERATED, ROLE_VENDORED, ROLE_DATA, ROLE_EXAMPLES,
    ROLE_SCRIPTS, ROLE_CI,
})

# ---------------------------------------------------------------------------
# Tier 1 — Path-based patterns (compiled regex, no I/O)
# ---------------------------------------------------------------------------
# Each entry is (compiled_pattern, role).  Patterns match against the
# normalised (forward-slash) relative path.  The first match wins, so
# ordering matters.

_PATH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # CI / CD directories
    (re.compile(r"(^|/)\.github/"), ROLE_CI),
    (re.compile(r"(^|/)\.circleci/"), ROLE_CI),
    (re.compile(r"(^|/)\.gitlab-ci/"), ROLE_CI),
    (re.compile(r"(^|/)\.gitlab/"), ROLE_CI),

    # Vendored / third-party
    (re.compile(r"(^|/)vendor/"), ROLE_VENDORED),
    (re.compile(r"(^|/)node_modules/"), ROLE_VENDORED),
    (re.compile(r"(^|/)third_party/"), ROLE_VENDORED),
    (re.compile(r"(^|/)third-party/"), ROLE_VENDORED),
    (re.compile(r"(^|/)extern/"), ROLE_VENDORED),
    (re.compile(r"(^|/)external/"), ROLE_VENDORED),

    # Test directories
    (re.compile(r"(^|/)tests/"), ROLE_TEST),
    (re.compile(r"(^|/)test/"), ROLE_TEST),
    (re.compile(r"(^|/)__tests__/"), ROLE_TEST),
    (re.compile(r"(^|/)spec/"), ROLE_TEST),
    (re.compile(r"(^|/)testing/"), ROLE_TEST),

    # Docs
    (re.compile(r"(^|/)docs/"), ROLE_DOCS),
    (re.compile(r"(^|/)doc/"), ROLE_DOCS),
    (re.compile(r"(^|/)documentation/"), ROLE_DOCS),

    # Examples / samples
    (re.compile(r"(^|/)examples/"), ROLE_EXAMPLES),
    (re.compile(r"(^|/)example/"), ROLE_EXAMPLES),
    (re.compile(r"(^|/)samples/"), ROLE_EXAMPLES),
    (re.compile(r"(^|/)sample/"), ROLE_EXAMPLES),

    # Scripts / bin
    (re.compile(r"(^|/)scripts/"), ROLE_SCRIPTS),
    (re.compile(r"(^|/)bin/"), ROLE_SCRIPTS),

    # Build / dist output
    (re.compile(r"(^|/)build/"), ROLE_BUILD),
    (re.compile(r"(^|/)dist/"), ROLE_BUILD),
    (re.compile(r"(^|/)out/"), ROLE_BUILD),
    (re.compile(r"(^|/)target/"), ROLE_BUILD),
]

# ---------------------------------------------------------------------------
# Tier 2 — Filename patterns (no I/O)
# ---------------------------------------------------------------------------
# Exact filename matches (case-insensitive) → role

_EXACT_FILENAMES: dict[str, str] = {
    "makefile": ROLE_BUILD,
    "dockerfile": ROLE_BUILD,
    "jenkinsfile": ROLE_BUILD,
    "vagrantfile": ROLE_BUILD,
    "rakefile": ROLE_BUILD,
    "gulpfile.js": ROLE_BUILD,
    "gruntfile.js": ROLE_BUILD,
    "webpack.config.js": ROLE_BUILD,
    "webpack.config.ts": ROLE_BUILD,
    "rollup.config.js": ROLE_BUILD,
    "rollup.config.ts": ROLE_BUILD,
    "vite.config.js": ROLE_BUILD,
    "vite.config.ts": ROLE_BUILD,
    "cmakelists.txt": ROLE_BUILD,
    "build.gradle": ROLE_BUILD,
    "build.gradle.kts": ROLE_BUILD,
    "pom.xml": ROLE_BUILD,
    "justfile": ROLE_BUILD,
    "taskfile.yml": ROLE_BUILD,
    "taskfile.yaml": ROLE_BUILD,
    "tiltfile": ROLE_BUILD,
    "procfile": ROLE_BUILD,
}

# Prefix-based filename matches (case-insensitive) → role
_FILENAME_PREFIXES: list[tuple[str, str]] = [
    ("readme", ROLE_DOCS),
    ("license", ROLE_DOCS),
    ("licence", ROLE_DOCS),
    ("changelog", ROLE_DOCS),
    ("contributing", ROLE_DOCS),
    ("authors", ROLE_DOCS),
    ("history", ROLE_DOCS),
    ("copying", ROLE_DOCS),
    ("code_of_conduct", ROLE_DOCS),
]

# Test filename patterns (compiled regex, matched against basename)
_TEST_PATTERNS: list[re.Pattern[str]] = [
    # Python: test_*.py, *_test.py
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),

    # Go: *_test.go
    re.compile(r"^.*_test\.go$"),

    # JavaScript / TypeScript: *.test.js, *.test.ts, *.test.jsx, *.test.tsx
    re.compile(r"^.*\.test\.[jt]sx?$"),
    # *.spec.js, *.spec.ts, *.spec.jsx, *.spec.tsx
    re.compile(r"^.*\.spec\.[jt]sx?$"),

    # Java: *Test.java, *Tests.java
    re.compile(r"^.*Tests?\.java$"),

    # Kotlin: *Test.kt, *Tests.kt
    re.compile(r"^.*Tests?\.kt$"),

    # C#: *Test.cs, *Tests.cs
    re.compile(r"^.*Tests?\.cs$"),

    # Ruby: *_spec.rb
    re.compile(r"^.*_spec\.rb$"),

    # PHP: *Test.php
    re.compile(r"^.*Test\.php$"),

    # Scala: *Test.scala, *Spec.scala
    re.compile(r"^.*(?:Test|Spec)\.scala$"),

    # Elixir: *_test.exs
    re.compile(r"^.*_test\.exs$"),

    # Dart: *_test.dart
    re.compile(r"^.*_test\.dart$"),

    # Salesforce Apex: *Test.cls
    re.compile(r"^.*Test\.cls$"),

    # Rust: tests are usually inline, but test files exist
    re.compile(r"^.*_test\.rs$"),

    # Swift: *Tests.swift, *Test.swift
    re.compile(r"^.*Tests?\.swift$"),
]

# Extension-based classification (matched against lowercase extension)
_DOC_EXTENSIONS = frozenset({".md", ".rst", ".adoc", ".asciidoc", ".txt"})

_CONFIG_EXTENSIONS = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".properties", ".env", ".editorconfig",
    ".xml",  # many config files are XML
})

# Config filenames that are config even without a typical config extension
_CONFIG_FILENAMES = frozenset({
    ".gitignore", ".gitattributes", ".dockerignore",
    ".eslintrc", ".prettierrc", ".babelrc",
    ".flake8", ".pylintrc", ".rubocop.yml",
    "setup.cfg", "pyproject.toml", "setup.py",
    "package.json", "tsconfig.json", "jsconfig.json",
    ".eslintrc.json", ".prettierrc.json",
    "tox.ini", "mypy.ini", "pytest.ini",
    "cargo.toml", "go.mod", "go.sum",
    "gemfile", "composer.json", "mix.exs",
    "pubspec.yaml", "pubspec.yml",
    ".htaccess", "nginx.conf",
    ".browserslistrc", ".nvmrc", ".node-version",
    ".python-version", ".ruby-version", ".tool-versions",
    "requirements.txt", "constraints.txt",
    "pipfile",
})

_DATA_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".bmp", ".webp",
    ".tiff", ".tif", ".psd",
    ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov", ".mkv",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".tsv", ".parquet", ".avro",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".dat", ".pak", ".wasm",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".class", ".jar",
})

# Filename patterns matched against basename (compiled regex, returns role)
_FILENAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # CI files at project root
    (re.compile(r"^\.gitlab-ci\.yml$"), ROLE_CI),
    (re.compile(r"^\.travis\.yml$"), ROLE_CI),
    (re.compile(r"^appveyor\.yml$"), ROLE_CI),
    (re.compile(r"^azure-pipelines\.yml$"), ROLE_CI),
    (re.compile(r"^bitbucket-pipelines\.yml$"), ROLE_CI),
    (re.compile(r"^cloudbuild\.yaml$"), ROLE_CI),
    (re.compile(r"^\.drone\.yml$"), ROLE_CI),
    (re.compile(r"^Jenkinsfile"), ROLE_CI),
    (re.compile(r"^codecov\.yml$"), ROLE_CI),
    (re.compile(r"^\.coveragerc$"), ROLE_CI),

    # Generated file patterns
    (re.compile(r"^.*\.generated\.\w+$"), ROLE_GENERATED),
    (re.compile(r"^.*\.g\.\w+$"), ROLE_GENERATED),
    (re.compile(r"^.*\.pb\.go$"), ROLE_GENERATED),
    (re.compile(r"^.*_pb2\.py$"), ROLE_GENERATED),
    (re.compile(r"^.*\.pb\.h$"), ROLE_GENERATED),
    (re.compile(r"^.*\.pb\.cc$"), ROLE_GENERATED),
    (re.compile(r"^.*\.min\.\w+$"), ROLE_GENERATED),

    # Lock files → config (they are dependency-version pinning)
    (re.compile(r"^.*\.lock$"), ROLE_CONFIG),
    (re.compile(r"^.*-lock\.\w+$"), ROLE_CONFIG),
]

# ---------------------------------------------------------------------------
# Tier 3 — Content-based patterns (selective I/O)
# ---------------------------------------------------------------------------

_GENERATED_MARKERS = re.compile(
    r"DO NOT EDIT|generated by|auto-generated|@generated|"
    r"GENERATED FILE|THIS FILE IS GENERATED|machine generated|"
    r"code generated|automatically generated",
    re.IGNORECASE,
)

_SHEBANG_PATTERN = re.compile(r"^#!.*/")

# Extensions eligible for minification detection
_MINIFIABLE_EXTENSIONS = frozenset({".js", ".css"})

_MINIFICATION_AVG_LINE_THRESHOLD = 110


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_path(path: str) -> str:
    """Normalise path separators to forward slashes."""
    return path.replace("\\", "/")


def _get_parts(normalised_path: str) -> tuple[str, str, str]:
    """Return (dir_part, basename, extension_lower) from a normalised path.

    dir_part includes trailing slash if present.
    """
    basename = os.path.basename(normalised_path)
    ext = os.path.splitext(basename)[1].lower()
    dir_part = normalised_path[: len(normalised_path) - len(basename)]
    return dir_part, basename, ext


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

def _tier1_path(normalised: str) -> str | None:
    """Tier 1: classify by directory path patterns."""
    for pattern, role in _PATH_PATTERNS:
        if pattern.search(normalised):
            return role
    return None


def _tier2_filename(basename: str, ext: str, normalised: str) -> str | None:
    """Tier 2: classify by filename and extension (no I/O)."""
    lower_name = basename.lower()

    # Exact filename match
    role = _EXACT_FILENAMES.get(lower_name)
    if role is not None:
        return role

    # Filename-pattern regex matches (CI, generated, lock)
    for pattern, role in _FILENAME_PATTERNS:
        if pattern.match(lower_name):
            return role

    # Prefix matches (docs: README*, LICENSE*, ...)
    for prefix, role in _FILENAME_PREFIXES:
        if lower_name.startswith(prefix):
            return role

    # Test filename patterns
    for pattern in _TEST_PATTERNS:
        if pattern.match(basename):
            return ROLE_TEST

    # Data files (binary / media)
    if ext in _DATA_EXTENSIONS:
        return ROLE_DATA

    # Doc extensions
    if ext in _DOC_EXTENSIONS:
        return ROLE_DOCS

    # Config: by well-known filename
    if lower_name in _CONFIG_FILENAMES:
        return ROLE_CONFIG

    # Config: by extension — but not if already classified as test/vendor
    if ext in _CONFIG_EXTENSIONS:
        # Don't reclassify files in test or vendor dirs as config
        path_role = _tier1_path(normalised)
        if path_role in (ROLE_TEST, ROLE_VENDORED):
            return path_role
        return ROLE_CONFIG

    return None


def _tier3_content(content: str | None, ext: str) -> str | None:
    """Tier 3: classify by file content (selective I/O)."""
    if content is None:
        return None

    lines = content.split("\n")

    # Check first 10 lines for generated markers
    head = "\n".join(lines[:10])
    if _GENERATED_MARKERS.search(head):
        return ROLE_GENERATED

    # Shebang → scripts
    if lines and _SHEBANG_PATTERN.match(lines[0]):
        return ROLE_SCRIPTS

    # Minification detection for .js / .css
    if ext in _MINIFIABLE_EXTENSIONS and lines:
        non_empty = [ln for ln in lines if ln.strip()]
        if non_empty:
            avg_len = sum(len(ln) for ln in non_empty) / len(non_empty)
            if avg_len > _MINIFICATION_AVG_LINE_THRESHOLD:
                return ROLE_GENERATED

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_file(path: str, content: str | None = None) -> str:
    """Classify a file into a role category.

    Uses a three-tier heuristic system:
      Tier 1 — Path-based patterns (~90% coverage, no I/O)
      Tier 2 — Filename + extension patterns (no I/O)
      Tier 3 — Content-based detection (selective, only for ambiguous files)

    Priority resolution (higher wins):
      1. Generated (content-detected)
      2. Vendored (path-detected)
      3. Test (name/path patterns)
      4. Build / CI (exact filename/path)
      5. Docs, Config, Examples, Scripts, Data
      6. Source (default for all code files)

    Args:
        path: relative file path (e.g., "src/main.py", "tests/test_foo.py")
        content: optional file content (or first N lines) for content-based
                 detection.  Pass None to skip Tier 3.

    Returns:
        role string: one of "source", "test", "config", "build", "docs",
                     "generated", "vendored", "data", "examples", "scripts",
                     "ci".
    """
    normalised = _normalise_path(path)
    _dir, basename, ext = _get_parts(normalised)

    # --- Content-based generated detection (highest priority) ---
    content_role = _tier3_content(content, ext)
    if content_role == ROLE_GENERATED:
        return ROLE_GENERATED

    # --- Path-based detection (vendored, test, build, ci, etc.) ---
    path_role = _tier1_path(normalised)

    # Vendored takes priority after generated
    if path_role == ROLE_VENDORED:
        return ROLE_VENDORED

    # Test by path takes priority
    if path_role == ROLE_TEST:
        return ROLE_TEST

    # --- Filename / extension based detection ---
    name_role = _tier2_filename(basename, ext, normalised)

    # Test by filename pattern
    if name_role == ROLE_TEST:
        return ROLE_TEST

    # Build / CI
    if path_role == ROLE_CI or name_role == ROLE_CI:
        return ROLE_CI
    if path_role == ROLE_BUILD or name_role == ROLE_BUILD:
        return ROLE_BUILD

    # Generated by filename pattern (e.g., *.min.js, *.pb.go)
    if name_role == ROLE_GENERATED:
        return ROLE_GENERATED

    # Remaining Tier 2 results
    if name_role is not None:
        return name_role

    # Remaining Tier 1 results (docs, examples, scripts)
    if path_role is not None:
        return path_role

    # Tier 3 content fallback (shebang → scripts)
    if content_role is not None:
        return content_role

    # Default: source code
    return ROLE_SOURCE


def is_test(path: str) -> bool:
    """Check if a file is a test file.

    Uses both path-based and filename-based patterns (Tier 1 + Tier 2).
    Does not require file content.
    """
    normalised = _normalise_path(path)
    _dir, basename, _ext = _get_parts(normalised)

    # Path-based check
    for pattern, role in _PATH_PATTERNS:
        if role == ROLE_TEST and pattern.search(normalised):
            return True

    # Filename-based check
    for pattern in _TEST_PATTERNS:
        if pattern.match(basename):
            return True

    return False


def is_source(path: str) -> bool:
    """Check if a file is source code.

    A file is source if it is NOT classified as test, config, docs,
    generated, vendored, data, examples, scripts, ci, or build.
    Content-based checks are skipped (no I/O).
    """
    return classify_file(path) == ROLE_SOURCE


def is_generated(path: str, content: str | None = None) -> bool:
    """Check if a file is generated code.

    Without content, only filename-based patterns are checked (e.g.,
    ``*.min.js``, ``*.pb.go``).  Pass content (first ~10 lines) for
    header-marker detection.
    """
    return classify_file(path, content) == ROLE_GENERATED


def is_vendored(path: str) -> bool:
    """Check if a file is vendored/third-party code."""
    normalised = _normalise_path(path)
    for pattern, role in _PATH_PATTERNS:
        if role == ROLE_VENDORED and pattern.search(normalised):
            return True
    return False
