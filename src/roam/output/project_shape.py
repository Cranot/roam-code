"""Project-shape detector — one place that answers "what kind of repo is this?".

Several commands have made bad assumptions:

* ``roam describe`` / ``roam preflight`` hardcode ``pytest tests/`` as the
  test command even on Vue/Vitest projects redacted).
* ``roam bus-factor`` warns about bus-factor=1 on every directory of a
  single-author project, drowning the actually-interesting STALE
  modules redacted).
* ``roam alerts`` thresholds are absolute and never adapt to project size
  or shape redacted).

Rather than each command re-inventing detection, they consult
:func:`detect_project_shape` and adapt their behaviour.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Recognise the most common test runners by their package.json scripts.test
# command shape. Order matters — the first match wins.
_TEST_RUNNER_HINTS = (
    ("vitest", ("vitest",)),
    ("jest", ("jest",)),
    ("mocha", ("mocha",)),
    ("playwright", ("playwright test",)),
    ("cypress", ("cypress run", "cypress open")),
    ("ava", ("ava",)),
    ("tap", (" tap ", "tap ")),
    ("karma", ("karma start",)),
    ("pytest", ("pytest",)),
    ("unittest", ("python -m unittest", "unittest")),
    ("rspec", ("rspec",)),
    ("go test", ("go test",)),
    ("cargo test", ("cargo test",)),
    ("dotnet test", ("dotnet test",)),
    ("phpunit", ("phpunit",)),
    ("mix test", ("mix test",)),
)

_BUILD_TOOL_HINTS = {
    "vite": ("vite.config.js", "vite.config.ts", "vite.config.mjs"),
    "webpack": ("webpack.config.js", "webpack.config.ts"),
    "rollup": ("rollup.config.js", "rollup.config.ts"),
    "esbuild": ("esbuild.js", "esbuild.config.js"),
    "turbopack": ("turbo.json",),
    "tsup": ("tsup.config.ts", "tsup.config.js"),
    "next": ("next.config.js", "next.config.ts", "next.config.mjs"),
    "nuxt": ("nuxt.config.js", "nuxt.config.ts"),
    "remix": ("remix.config.js",),
    "angular": ("angular.json",),
    "cargo": ("Cargo.toml",),
    "go": ("go.mod",),
    "maven": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts"),
    "pip": ("pyproject.toml", "setup.py", "setup.cfg"),
    "poetry": ("pyproject.toml",),  # narrow to poetry section in detect()
    "composer": ("composer.json",),
    "dotnet": ("*.csproj", "*.sln", "*.fsproj"),
}

_PACKAGE_MANAGER_LOCKFILES = (
    ("pnpm", "pnpm-lock.yaml"),
    ("yarn", "yarn.lock"),
    ("bun", "bun.lockb"),
    ("npm", "package-lock.json"),
    ("poetry", "poetry.lock"),
    ("uv", "uv.lock"),
    ("pip", "requirements.txt"),
    ("cargo", "Cargo.lock"),
    ("go", "go.sum"),
    ("composer", "composer.lock"),
)

# When a single author owns at least this fraction of commits, the project
# is treated as single-author. ``bus-factor`` and ``ownership`` switch to
# stale-module mode rather than warning about every "bus factor 1" file.
_SINGLE_AUTHOR_THRESHOLD = 0.80
# Two or fewer dominant authors covering at least this share of commits.
_SMALL_TEAM_THRESHOLD = 0.85


@dataclass
class ProjectShape:
    """Summary of the repo's structural and social shape."""

    primary_language: str | None = None
    languages: list[tuple[str, int]] = field(default_factory=list)
    polyglot: bool = False
    test_runner: str | None = None
    test_command: str | None = None
    build_tool: str | None = None
    package_manager: str | None = None
    has_frontend: bool = False
    has_backend: bool = False
    team_size: str = "unknown"  # 'single-author' | 'small-team' | 'distributed' | 'unknown'
    dominant_authors: list[tuple[str, int]] = field(default_factory=list)
    file_count: int = 0
    symbol_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _read_package_json(root: Path) -> dict | None:
    pkg = root / "package.json"
    if not pkg.exists():
        return None
    try:
        return json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _detect_test_runner(root: Path) -> tuple[str | None, str | None]:
    """Return (runner, exact_command). Falls back to (None, None)."""
    pkg = _read_package_json(root)
    if pkg:
        scripts = pkg.get("scripts") or {}
        test_cmd = scripts.get("test") or scripts.get("test:unit") or scripts.get("test:run")
        if isinstance(test_cmd, str) and test_cmd.strip():
            cmd_lower = test_cmd.lower()
            for runner, needles in _TEST_RUNNER_HINTS:
                if any(needle in cmd_lower for needle in needles):
                    return runner, test_cmd
            return "npm-run-test", test_cmd

    # No package.json or no test script — fall back to file extensions.
    for sql in ("pyproject.toml", "setup.cfg", "tox.ini"):
        if (root / sql).exists():
            return "pytest", "pytest tests/"
    if (root / "go.mod").exists():
        return "go test", "go test ./..."
    if (root / "Cargo.toml").exists():
        return "cargo test", "cargo test"
    if (root / "Gemfile").exists():
        return "rspec", "bundle exec rspec"
    return None, None


def _detect_package_manager(root: Path) -> str | None:
    for name, lockfile in _PACKAGE_MANAGER_LOCKFILES:
        if (root / lockfile).exists():
            return name
    return None


def _detect_build_tool(root: Path) -> str | None:
    for tool, files in _BUILD_TOOL_HINTS.items():
        for f in files:
            if "*" in f:
                if any(root.glob(f)):
                    return tool
            elif (root / f).exists():
                return tool
    return None


def _language_distribution(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT COALESCE(language, '') AS lang, COUNT(*) AS n "
        "FROM files WHERE COALESCE(language, '') != '' "
        "GROUP BY language ORDER BY n DESC"
    ).fetchall()
    return [(r["lang"], r["n"]) for r in rows]


def _author_distribution(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    try:
        rows = conn.execute(
            "SELECT author, COUNT(*) AS commits FROM git_commits "
            "WHERE author IS NOT NULL AND author != '' "
            "GROUP BY author ORDER BY commits DESC LIMIT 10"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [(r["author"], r["commits"]) for r in rows]


def _team_size_label(authors: list[tuple[str, int]]) -> str:
    if not authors:
        return "unknown"
    total = sum(n for _, n in authors)
    if total == 0:
        return "unknown"
    top = authors[0][1] / total
    if top >= _SINGLE_AUTHOR_THRESHOLD:
        return "single-author"
    top_two = sum(n for _, n in authors[:2]) / total
    if top_two >= _SMALL_TEAM_THRESHOLD:
        return "small-team"
    return "distributed"


_FRONTEND_LANGS = frozenset({"javascript", "typescript", "tsx", "jsx", "vue", "svelte"})
_BACKEND_LANGS = frozenset({"python", "go", "rust", "java", "kotlin", "ruby", "php", "csharp", "scala", "elixir"})


def detect_project_shape(conn: sqlite3.Connection, project_root: Path) -> ProjectShape:
    """Detect the repo's shape so consumers can adapt their behaviour.

    Cheap to call: a dozen file stats + two SQL aggregations. Cache it
    per process if you call it multiple times in one command.
    """
    languages = _language_distribution(conn)
    primary = languages[0][0] if languages else None
    polyglot = len(languages) >= 2

    runner, test_cmd = _detect_test_runner(project_root)
    build = _detect_build_tool(project_root)
    pkg_mgr = _detect_package_manager(project_root)

    lang_set = {lang for lang, _ in languages}
    has_frontend = bool(lang_set & _FRONTEND_LANGS)
    has_backend = bool(lang_set & _BACKEND_LANGS)

    authors = _author_distribution(conn)
    team_size = _team_size_label(authors)

    try:
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 0
    except sqlite3.Error:
        file_count = 0
    try:
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 0
    except sqlite3.Error:
        sym_count = 0

    return ProjectShape(
        primary_language=primary,
        languages=languages,
        polyglot=polyglot,
        test_runner=runner,
        test_command=test_cmd,
        build_tool=build,
        package_manager=pkg_mgr,
        has_frontend=has_frontend,
        has_backend=has_backend,
        team_size=team_size,
        dominant_authors=authors[:5],
        file_count=file_count,
        symbol_count=sym_count,
    )
