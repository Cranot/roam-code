"""``roam orphan-imports`` — imports that don't resolve to a known module.

quick lint that catches:

* ``import x.y`` / ``from x.y import z`` where ``x.y`` is neither in the
  indexed source tree nor a stdlib / installed package.
* Typo'd local imports (e.g. ``from roam.cmds.foo import bar`` when the
  module is ``roam.commands.cmd_foo``).

extended to JS / TS (``import 'x'``) and Go (``import "x"``).
Each language gets its own ``_indexed_modules`` accumulator and import
regex; the resolution rules differ per language.
"""

from __future__ import annotations

import ast
import importlib.util
import json as _json
import re
import sqlite3
import sys
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import json_envelope, to_json

# W132 (W93 follow-up): orphan-imports is the fifth detector migrating
# onto the central findings registry (after ``clones`` in W95, ``dead``
# in W99, ``complexity`` in W102, ``smells`` in W109). The shape mirrors
# those four — a stable detector version stamp and a deterministic
# ``finding_id_str`` so re-runs upsert instead of duplicating rows.
# Bump this when the orphan-classifier predicates / kind enumeration /
# claim shape changes meaningfully.
ORPHAN_IMPORTS_DETECTOR_VERSION: str = "1.0.0"


# W132 — per-orphan-kind confidence tier mapping.
#
# All three current kinds are deterministic static analyses:
#
# * ``internal_typo`` — Python: top-level package IS in the indexed
#   module set but the full dotted path is NOT. Pure set membership over
#   the index → ``static_analysis``.
# * ``missing_package`` — Python: dotted path resolves neither in the
#   index NOR via ``importlib.util.find_spec``. Two deterministic
#   lookups, no name-pattern guessing → ``static_analysis``.
# * ``missing_local`` — JS/Go: relative or path-style import that
#   doesn't resolve to an indexed file / package. Deterministic path
#   normalisation against the index → ``static_analysis``.
#
# A future heuristic kind (e.g. "this import LOOKS unused but may be a
# side-effect import") would land at ``heuristic`` — captured by the
# default fallback below.
_ORPHAN_KIND_TO_CONFIDENCE: dict[str, str] = {
    "internal_typo": "static_analysis",
    "missing_package": "static_analysis",
    "missing_local": "static_analysis",
}
_ORPHAN_DEFAULT_CONFIDENCE: str = "heuristic"


def _orphan_finding_id(language: str, file_path: str, module: str, line: int | None) -> str:
    """Stable, deterministic finding id for one orphan-import hit.

    The (language, file_path, module, line) tuple re-identifies the same
    orphan across runs. We fold all four into the digest so:

    * The same import on the same line upserts in place across re-runs.
    * Two distinct orphan imports in the same file at different lines
      get distinct ids (e.g. two typo'd imports in one module).
    * The same module name appearing in two files gets two distinct
      registry rows (each file's import is its own finding).
    """
    from roam.db.findings import make_finding_id

    return make_finding_id("orphan-imports", language, language, file_path, module, int(line or 0))


def _resolve_orphan_subject_id(conn: sqlite3.Connection, file_path: str) -> int | None:
    """Best-effort lookup of ``files.id`` for the importing file.

    Orphan-import findings are file-level, not symbol-level — the
    finding lives at "this file imports X that doesn't resolve", not
    at any particular symbol within. Returns ``None`` when the file
    isn't in the index (e.g. discovered by the filesystem fallback in
    ``_indexed_python_modules`` but not yet by the indexer pipeline).
    The registry permits NULL subject_id by design.
    """
    try:
        row = conn.execute(
            "SELECT id FROM files WHERE path = ? LIMIT 1",
            (file_path,),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _emit_orphan_imports_findings(
    conn: sqlite3.Connection,
    orphans: list[dict],
    source_version: str,
) -> int:
    """Mirror each orphan-import finding into the central findings registry.

    Returns the count of finding rows written. Caller is responsible
    for opening ``conn`` writable; emit_finding does not commit
    (the caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard orphan-imports command path.
    """
    # Local import keeps the cost out of the read-only path —
    # callers without --persist never reach here.
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for o in orphans:
        language = o.get("language") or "unknown"
        file_path = o.get("file") or ""
        module = o.get("module") or ""
        kind = o.get("kind") or ""
        line = o.get("line")
        hint = o.get("hint") or ""

        subject_id = _resolve_orphan_subject_id(conn, file_path)
        finding_id = _orphan_finding_id(language, file_path, module, line)
        evidence = {
            "language": language,
            "file": file_path,
            "line": line,
            "module": module,
            "kind": kind,
            "hint": hint,
        }
        claim = (
            f"orphan-import ({kind}): {language} module {module!r} in {file_path}:{line} — {hint or 'no resolution'}"
        )
        confidence = _ORPHAN_KIND_TO_CONFIDENCE.get(kind, _ORPHAN_DEFAULT_CONFIDENCE)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file" if subject_id is not None else "module",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="orphan-imports",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# R22 — confidence-derivation rule for orphan imports:
#   "internal_typo" (Python: top-level package is indexed but the
#     dotted submodule isn't) → "high". The package exists; this is
#     almost surely a stale import or typo.
#   "missing_package" (Python: importlib can't find anything matching
#     even at top level) → "medium". Could be a typo, could be an
#     uninstalled optional dep — agent should investigate.
#   "missing_local" (JS/Go: relative path or local package not in
#     indexed files) → "medium". Same uncertainty as missing_package
#     but with slightly higher build-tool surface area.
_ORPHAN_KIND_CONFIDENCE = {
    "internal_typo": "high",
    "missing_package": "medium",
    "missing_local": "medium",
}


def _orphan_classify(o: dict) -> tuple[str, str]:
    """Map an orphan-import finding to a (confidence, reason) tuple."""
    kind = (o.get("kind") or "").lower()
    conf = _ORPHAN_KIND_CONFIDENCE.get(kind, "low")
    lang = o.get("language", "?")
    module = o.get("module", "?")
    if kind == "internal_typo":
        reason = (
            f"{lang}: top-level package for '{module}' is indexed but "
            f"the full dotted path is not — almost certainly a typo or "
            f"stale import"
        )
    elif kind == "missing_package":
        reason = (
            f"{lang}: '{module}' resolves neither in the index nor via "
            f"importlib — likely typo or uninstalled dependency"
        )
    elif kind == "missing_local":
        reason = (
            f"{lang}: '{module}' is a path-style import that doesn't "
            f"resolve to an indexed file — possible build-tool resolution"
        )
    else:
        reason = f"{lang}: unknown orphan kind '{kind}'"
    return conf, reason


# Python ----------------------------------------------------------------------

_PY_IMPORT_RE = re.compile(
    r"^[ \t]*(?:import[ \t]+([\w.]+)|from[ \t]+([\w.]+)[ \t]+import[ \t]+)",
    re.MULTILINE,
)
_PY_RELATIVE_PREFIX_RE = re.compile(r"^[ \t]*from[ \t]+\.")

# W159: Strip triple-quoted strings and ``#`` line comments BEFORE running
# the import regex. Without this, prose inside a module docstring like
# "...not visible from any\nimport or call edge..." matches the
# ``^[ \t]*import[ \t]+([\w.]+)`` arm and produces a phantom ``or`` orphan
# import. Newlines are preserved so the line-number arithmetic downstream
# stays accurate. Single-line string literals can't span newlines so they
# can't fake an ``^import`` match — only triple-quoted strings need masking.
# Paired with the ``^[ \t]*`` (not ``^\s*``) in ``_PY_IMPORT_RE`` /
# ``_PY_RELATIVE_PREFIX_RE`` above: ``\s`` includes ``\n`` so the previous
# pattern would greedily span blank lines (e.g. blanked-out comment lines)
# back to the prior line boundary, causing the import-line slice to fall
# in the wrong place and break the relative-prefix filter on indented
# ``from .foo import ...`` statements.
_PY_TRIPLE_QUOTED_RE = re.compile(
    r'(?:r?b?|b?r?)("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')',
)
_PY_LINE_COMMENT_RE = re.compile(r"#[^\n]*")


def _blank_preserving_newlines(match: re.Match) -> str:
    """Replace a match with spaces, preserving newlines.

    Used by ``_mask_python_strings_and_comments`` so the masked text has
    the same length and line layout as the original — downstream regex
    line counters keep working unchanged.
    """
    return "".join(" " if ch != "\n" else "\n" for ch in match.group(0))


def _mask_python_strings_and_comments(text: str) -> str:
    """Return ``text`` with triple-quoted strings + ``#`` comments blanked.

    The masked output preserves byte offsets and line layout — only the
    content inside strings/comments is replaced with spaces. This blocks
    the W159 phantom-import bug where ``import or`` prose inside a
    module docstring leaks through the ``_PY_IMPORT_RE`` scan.
    """
    masked = _PY_TRIPLE_QUOTED_RE.sub(_blank_preserving_newlines, text)
    masked = _PY_LINE_COMMENT_RE.sub(_blank_preserving_newlines, masked)
    return masked


# JS/TS — capture the bare module specifier in any of the four shapes:
#   import x from 'pkg';        import 'pkg';          import * as a from "pkg";
#   import {a, b} from "@scope/pkg/sub";   const x = require("pkg");
_JS_IMPORT_RE = re.compile(
    r"""(?:^|[\s;])(?:import\s+(?:[^'"]+\s+from\s+)?|require\s*\()\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# Go — `import "pkg"` and grouped imports inside `import ( ... )`.
_GO_IMPORT_LINE_RE = re.compile(r'^\s*(?:_\s+|[\w]+\s+)?"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK_RE = re.compile(r"^\s*import\s*\(([^)]*)\)", re.MULTILINE | re.DOTALL)
_GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+(?:_\s+|[\w]+\s+)?"([^"]+)"', re.MULTILINE)

# Module names — common stdlib heuristics for non-Python so we don't flag
# everyday standard imports as orphans.
_GO_STDLIB = frozenset(
    {
        "fmt",
        "os",
        "io",
        "net",
        "errors",
        "strings",
        "strconv",
        "time",
        "context",
        "encoding",
        "log",
        "math",
        "sync",
        "regexp",
        "bytes",
        "path",
        "sort",
        "bufio",
        "testing",
        "reflect",
        "html",
        "database",
        "crypto",
        "container",
        "compress",
        "archive",
        "image",
        "runtime",
        "go",
        "flag",
        "unicode",
        "syscall",
        "hash",
        "embed",
        "iter",
        "slices",
        "maps",
        "cmp",
        "internal",
        "encoding/json",
        "net/http",
        "net/url",
    }
)
_JS_BUILTIN_PREFIXES = (
    "node:",
    "fs",
    "path",
    "child_process",
    "stream",
    "events",
    "util",
    "http",
    "https",
    "crypto",
    "url",
    "os",
    "process",
)

# Style/asset imports (``import './styles.css'``) resolve to a real on-disk
# file, not to an indexed JS/TS module — the indexer never registers CSS as a
# JS-family language, so a naive ``target in indexed`` check flags every
# existing stylesheet as a missing_local orphan. Dogfooded 2026-07-15. We
# resolve these against the filesystem before flagging.
_STYLE_IMPORT_EXTENSIONS: tuple[str, ...] = (".css", ".scss", ".sass", ".less")


def _style_asset_resolves(target: str) -> bool:
    """True when a relative style import (.css/.scss/.sass/.less) exists on disk.

    ``target`` is the normalised posix path of the import relative to the
    project root (the scanner runs with CWD == project root, mirroring the
    ``Path(rel_path).is_file()`` check the scanners already rely on).
    """
    if not target.lower().endswith(_STYLE_IMPORT_EXTENSIONS):
        return False
    try:
        return Path(target).is_file()
    except OSError:
        return False


def _modules_from_path(rel_path: str, out: set[str]) -> None:
    """Add every dotted-prefix module name derivable from a Python file path."""
    parts = Path(rel_path).with_suffix("").parts
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return
    out.add(".".join(parts))
    for i in range(1, len(parts)):
        out.add(".".join(parts[:i]))


def _indexed_python_modules(conn) -> set[str]:
    """Return dotted-module names derivable from indexed Python files.

    also walks ``src/`` directly (filesystem) so modules
    added since the last index don't get falsely flagged as
    internal-typo orphans. The DB query catches the bulk; the filesystem
    walk fills in any new files the index hasn't seen yet.
    """
    out: set[str] = set()
    rows = conn.execute(
        """
        SELECT path FROM files
         WHERE language = 'python'
           AND path NOT LIKE 'tests/%'
           AND path NOT LIKE 'tests\\%'
           AND path NOT LIKE 'benchmarks/%'
           AND path NOT LIKE 'benchmarks\\%'
        """
    ).fetchall()
    for r in rows:
        _modules_from_path(r[0], out)

    # filesystem fallback for newly-added files.
    src_root = Path("src")
    if src_root.is_dir():
        for py in src_root.rglob("*.py"):
            try:
                rel = py.relative_to(Path(".")).as_posix()
            except ValueError:
                rel = py.as_posix()
            _modules_from_path(rel, out)
    return out


def _indexed_python_subtree_modules(conn, subtree: str) -> set[str]:
    """Return dotted-module names derivable from Python files under ``subtree``.

    W161 — companion to :func:`_indexed_python_modules`. The default
    indexed-modules set deliberately excludes ``tests/`` and
    ``benchmarks/`` so src-level imports of those paths don't masquerade
    as internal modules. But test-to-test imports (e.g.
    ``from tests._helpers.repo_root import …``) and benchmark-to-
    benchmark imports (``from prompts import …``) ARE valid and must
    resolve. This helper returns the subtree's own module set so the
    orphan scanner can merge it in when scanning a file in that subtree.

    For ``tests`` / ``benchmarks`` specifically, also adds the BARE
    filenames (without the subtree prefix). Pytest puts the test
    directory on ``sys.path`` at collection time, so a sibling
    ``from test_law4_lint import …`` resolves cleanly even though the
    indexed file path is ``tests/test_law4_lint.py``. Same for
    ``benchmarks/`` (script-style sibling imports run with the
    directory on the path).

    ``subtree`` is a literal path prefix like ``"tests"`` or
    ``"benchmarks"``. Returns an empty set when no files under that
    prefix are indexed.
    """
    out: set[str] = set()
    rows = conn.execute(
        "SELECT path FROM files WHERE language = 'python' AND (path LIKE ? OR path LIKE ?)",
        (f"{subtree}/%", f"{subtree}\\%"),
    ).fetchall()

    def _add(rel: str) -> None:
        _modules_from_path(rel, out)
        # Strip the subtree prefix and re-derive — covers bare sibling
        # imports under pytest's added-to-sys.path discovery.
        norm = rel.replace("\\", "/")
        if norm.startswith(f"{subtree}/"):
            inner = norm[len(subtree) + 1 :]
            if inner:
                _modules_from_path(inner, out)

    for r in rows:
        _add(r[0])

    # Filesystem fallback for newly-added files in the subtree.
    subtree_root = Path(subtree)
    if subtree_root.is_dir():
        for py in subtree_root.rglob("*.py"):
            try:
                rel = py.relative_to(Path(".")).as_posix()
            except ValueError:
                rel = py.as_posix()
            _add(rel)
    return out


def _indexed_js_modules(conn) -> set[str]:
    """Return path-style module identifiers for indexed JS/TS/Vue/Svelte files.

    A JS import like ``./utils/format`` or ``../models/user`` resolves
    relative to the importing file. Path-relative imports we keep as-is
    (they're handled per-file). Bare specifiers like ``react`` or
    ``@scope/pkg`` are treated as external packages by default; we
    flag them only when no node_modules signal exists. Indexed modules
    are accumulated as posix paths *without* extension so an import of
    ``./utils/format`` matches ``src/utils/format.ts``.

    Vue/Svelte SFCs are first-class JS module citizens: importing a
    ``.vue`` file resolves to the file path itself (the extension is
    typically retained at the import site, e.g.
    ``import Bar from './Bar.vue'``) and the extension-less variant
    also resolves under common bundler configurations.
    """
    out: set[str] = set()
    ph = ",".join("?" * len(JS_FAMILY_LANGUAGES))
    rows = conn.execute(
        f"SELECT path FROM files WHERE language IN ({ph})",
        JS_FAMILY_LANGUAGES,
    ).fetchall()
    for r in rows:
        path = (r[0] or "").replace("\\", "/")
        # Register the path *with* the .vue / .svelte extension so explicit
        # `import Bar from './Bar.vue'` resolves directly.
        out.add(path)
        # Strip extension and `index` filename so extension-less imports
        # (`./utils/format`) match too. Includes .vue / .svelte for
        # bundlers that allow omitting those extensions.
        without_ext = re.sub(r"\.(js|jsx|ts|tsx|mjs|cjs|vue|svelte)$", "", path)
        out.add(without_ext)
        if without_ext.endswith("/index"):
            out.add(without_ext[: -len("/index")])
    return out


def _indexed_go_packages(conn) -> set[str]:
    """Return Go import paths derivable from indexed .go files.

    Without `go.mod` parsing this is best-effort: we register every
    directory containing at least one .go file as a possible local
    package path. External imports are caught by stdlib + 'looks like
    domain.com/...' heuristics.
    """
    out: set[str] = set()
    rows = conn.execute("SELECT path FROM files WHERE language = 'go'").fetchall()
    for r in rows:
        path = (r[0] or "").replace("\\", "/")
        parts = path.split("/")
        if len(parts) > 1:
            out.add("/".join(parts[:-1]))
    return out


# W161 — distribution-name → import-name overrides for the pyproject filter
# below. PEP 621 metadata lists *distribution* names (the ``pip install X``
# string); ``import`` statements use the *import* name. Most packages match
# (``click`` ↔ ``click``) but a small set deliberately doesn't. The map
# below covers the high-frequency cases that surfaced as orphan-import
# false positives on real projects when roam runs in a sibling venv that
# lacks the project's dev/optional deps installed.
#
# Source: PyPI metadata + common Python ecosystem conventions. Keep this
# list tight — every entry below is documented as a real distribution
# name whose top-level package is named differently.
_PEP621_DIST_TO_IMPORT_NAME: dict[str, tuple[str, ...]] = {
    "pyyaml": ("yaml",),
    "pillow": ("PIL",),
    "python-dateutil": ("dateutil",),
    "beautifulsoup4": ("bs4",),
    "msgpack-python": ("msgpack",),
    "scikit-learn": ("sklearn",),
    "opencv-python": ("cv2",),
    "ipython": ("IPython",),
    "attrs": ("attr", "attrs"),
    "pyjwt": ("jwt",),
    "pycryptodome": ("Crypto",),
    "protobuf": ("google",),  # imports as ``google.protobuf``
    "grpcio": ("grpc",),
    "grpcio-tools": ("grpc_tools",),
    "tree-sitter-language-pack": ("tree_sitter_language_pack",),
    "tree-sitter": ("tree_sitter",),
}


def _declared_python_dependencies(project_root: Path) -> frozenset[str]:
    """Return the set of top-level import names declared in ``pyproject.toml``.

    Reads ``[project.dependencies]`` and ``[project.optional-dependencies]``
    and converts each PEP 508 requirement string into the importable
    top-level package name (head of the dotted module). Distribution
    names that differ from their import name are translated via
    :data:`_PEP621_DIST_TO_IMPORT_NAME`.

    Use case (W161): roam may be installed via ``uv tool install`` /
    ``pipx`` into an isolated venv that does NOT have the project's
    dev/optional dependencies. ``importlib.util.find_spec("pytest")``
    returns ``None`` in that venv, so every ``import pytest`` in the
    project's test suite would be flagged as a missing-package orphan.
    This helper short-circuits that by trusting what the project itself
    declares as its dependency surface.

    Returns an empty set when ``pyproject.toml`` is absent or
    unparseable — the existing ``importlib`` + indexed-modules fallback
    in :func:`_is_external_python_package` still applies, so a broken
    pyproject doesn't suppress real orphan detection.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return frozenset()
    # tomllib is stdlib on 3.11+; 3.10 needs the tomli backport. Fall back
    # silently so the orphan-imports filter still works on 3.10 hosts that
    # ship without tomli — but in that case the pyproject filter is a no-op
    # and the caller sees the same behaviour as a missing pyproject.toml.
    try:
        import tomllib  # type: ignore[import-not-found]

        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except ImportError:
        try:
            import tomli  # type: ignore[import-not-found]
        except ImportError:
            return frozenset()
        try:
            with pyproject.open("rb") as f:
                data = tomli.load(f)
        except (OSError, tomli.TOMLDecodeError):
            return frozenset()
    except (OSError, tomllib.TOMLDecodeError):
        return frozenset()

    project = data.get("project", {}) or {}
    requirement_strings: list[str] = list(project.get("dependencies", []) or [])
    optional = project.get("optional-dependencies", {}) or {}
    for group in optional.values():
        if isinstance(group, list):
            requirement_strings.extend(group)

    names: set[str] = set()
    for req in requirement_strings:
        if not isinstance(req, str):
            continue
        # PEP 508 — extract the distribution name (everything before the
        # first ``[``, ``(``, version specifier, or whitespace marker).
        # We only need the *name*, not the version, so a cheap regex
        # split is enough.
        name = re.split(r"[\s\[\(=<>!~;]", req.strip(), maxsplit=1)[0].strip().lower()
        if not name:
            continue
        # Map known distribution-vs-import-name mismatches.
        mapped = _PEP621_DIST_TO_IMPORT_NAME.get(name)
        if mapped is not None:
            names.update(mapped)
        else:
            # Default: distribution name == import name with ``-`` → ``_``.
            names.add(name.replace("-", "_"))
    return frozenset(names)


# Module-level cache so repeated calls in the same scan don't re-read +
# re-parse pyproject.toml. Keyed on the resolved project root so the
# cache stays valid when tests run with chdir'd CWDs.
_DECLARED_DEPS_CACHE: dict[Path, frozenset[str]] = {}


def _is_external_python_package(module: str, project_root: Path | None = None) -> bool:
    head = module.split(".", 1)[0]
    if head in sys.builtin_module_names:
        return True
    # W161 — trust the project's declared dependencies before falling
    # back to ``importlib.util.find_spec``. This catches the case where
    # roam runs in a sibling venv (uv tool install / pipx) that doesn't
    # have the project's dev / optional deps installed.
    if project_root is not None:
        try:
            resolved = project_root.resolve()
        except OSError:
            resolved = project_root
        cached = _DECLARED_DEPS_CACHE.get(resolved)
        if cached is None:
            cached = _declared_python_dependencies(resolved)
            _DECLARED_DEPS_CACHE[resolved] = cached
        if head.lower() in cached or head in cached:
            return True
    try:
        return importlib.util.find_spec(head) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _is_external_go_package(module: str) -> bool:
    if module in _GO_STDLIB:
        return True
    head = module.split("/", 1)[0]
    if head in _GO_STDLIB:
        return True
    # Module path with a hostname-shaped first segment is almost
    # certainly a third-party Go module (github.com/x/y, gopkg.in/x.v1).
    if "." in head:
        return True
    return False


def _is_external_js_package(module: str) -> bool:
    if not module:
        return True
    if any(module.startswith(p) for p in _JS_BUILTIN_PREFIXES):
        return True
    # Bare specifier (no leading dot or slash) → npm package by default.
    if not module.startswith(".") and not module.startswith("/"):
        return True
    return False


# W160 — three false-positive filters for the Python scan.
#
# The 212-eval dogfood audit identified three noise sources that together
# accounted for 290+ of the ~344 orphan-imports findings on this very
# codebase: (1) pytest auto-discovered conftest modules, (2) optional
# dependencies wrapped in ``try: import x; except ImportError: ...``,
# (3) relative imports (``from .base import …``) the resolver couldn't
# walk back to the importing file's package. The three helpers below
# tag each respective case as RESOLVED so the orphan report keeps the
# genuine signal — typo'd or actually-missing imports.


def _is_conftest_path(
    dotted: str,
    project_root: Path,
    importing_file: Path | None = None,
) -> bool:
    """Pytest auto-discovers ``conftest.py`` — treat it as resolvable.

    A bare ``import conftest`` or a dotted ``from tests.conftest import …``
    has no real importable module on ``sys.path``; pytest injects it at
    collection time. The orphan-imports detector has no awareness of
    that machinery, so it flags every such import as ``missing_package``.

    Resolution rules (in order):

    1. **Dotted** (e.g. ``tests.conftest``): a ``conftest.py`` exists
       at the literal dotted path under ``project_root`` or ``src/``.
    2. **Bare** (``conftest``): a ``conftest.py`` exists in the
       importing file's directory OR any ancestor up to
       ``project_root``. This mirrors pytest's rootdir-walking
       behaviour — a test in ``tests/foo/test_x.py`` can
       ``import conftest`` and pytest resolves it via the nearest
       ``conftest.py``.
    3. **Fallback**: any ``conftest.py`` anywhere under the project
       root whose path tail matches the dotted prefix — covers layouts
       that nest conftests outside both root and ``src/``.
    """
    if dotted != "conftest" and not dotted.endswith(".conftest"):
        return False
    parts = dotted.split(".")
    if parts[:-1]:
        # Dotted form — walk the prefix down from project_root.
        candidate = project_root.joinpath(*parts[:-1]) / "conftest.py"
        if candidate.exists():
            return True
        src_candidate = project_root / "src" / Path(*parts[:-1]) / "conftest.py"
        if src_candidate.exists():
            return True
        # Fallback: any conftest.py whose path tail matches the dotted prefix.
        suffix = Path(*parts[:-1], "conftest.py")
        for found in project_root.rglob("conftest.py"):
            try:
                rel = found.relative_to(project_root)
            except ValueError:
                continue
            if rel.as_posix().endswith(suffix.as_posix()):
                return True
        return False
    # Bare ``import conftest`` — pytest finds the nearest conftest.py
    # by walking up from the test file's directory. Mirror that walk.
    if importing_file is not None:
        cur = importing_file.resolve().parent if not importing_file.is_absolute() else importing_file.parent
        # Clamp the walk to ``project_root`` so a path outside the project
        # doesn't chase the filesystem root.
        try:
            project_resolved = project_root.resolve()
        except OSError:
            project_resolved = project_root
        # Walk parents until we leave the project root.
        while True:
            if (cur / "conftest.py").exists():
                return True
            if cur == project_resolved or cur.parent == cur:
                break
            cur = cur.parent
    # Last resort: a conftest.py exists at root or under src/.
    if (project_root / "conftest.py").exists():
        return True
    if (project_root / "src" / "conftest.py").exists():
        return True
    return False


def _optional_import_line_set(source: str) -> set[int]:
    """Return the set of source lines that sit inside a try/except ImportError block.

    Python's optional-dependency idiom is::

        try:
            import numpy
        except ImportError:
            numpy = None

    These imports are expected to fail in some environments — reporting
    them as orphans is noise. We re-parse the source with ``ast`` (small
    files, acceptable cost) and collect every line covered by a
    ``Try`` node whose handlers catch ``ImportError`` /
    ``ModuleNotFoundError`` (or a bare ``except:`` / ``Exception``).
    The caller checks each import's line number against the set.

    Returns an empty set on SyntaxError — a syntactically broken file
    shouldn't crash the scan; the regex-based orphan detection on that
    file continues to surface whatever it finds.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    optional_lines: set[int] = set()
    _OPTIONAL_NAMES = {"ImportError", "ModuleNotFoundError", "Exception"}

    def _handler_catches_import_error(h: ast.ExceptHandler) -> bool:
        # ``except:`` (no type) — catches anything including ImportError.
        if h.type is None:
            return True
        # ``except ImportError:`` / ``except ModuleNotFoundError:`` etc.
        if isinstance(h.type, ast.Name):
            return h.type.id in _OPTIONAL_NAMES
        # ``except (ImportError, ModuleNotFoundError):`` etc.
        if isinstance(h.type, ast.Tuple):
            return any(isinstance(e, ast.Name) and e.id in _OPTIONAL_NAMES for e in h.type.elts)
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handler_catches_import_error(h) for h in node.handlers):
            continue
        if not node.body:
            continue
        # ast.Try.body spans from the first statement's lineno to the
        # last statement's end_lineno. Both are 1-based and inclusive.
        start = node.body[0].lineno
        end = node.body[-1].end_lineno or start
        for ln in range(start, end + 1):
            optional_lines.add(ln)
    return optional_lines


def _resolve_relative_import(dotted: str, importing_file: Path, project_root: Path) -> Path | None:
    """Resolve a dotted relative import (``.base``, ``..commands.resolve``).

    The current regex-based scanner ALREADY skips ``from .x import y``
    lines (via ``_PY_RELATIVE_PREFIX_RE``), but the ``_PY_IMPORT_RE``
    captures certain ``from .pkg.mod`` re-export modules whose dotted
    paths land in the orphan list as raw ``.x.y`` strings. This helper
    resolves a relative dotted path against ``importing_file``'s
    package and returns the target file path (or None when
    unresolvable). It exists so a stray ``.base`` capture doesn't bloat
    the orphan list.

    Walks up ``n_dots - 1`` levels from ``importing_file.parent`` so
    that:

      * ``.base`` from ``src/roam/languages/python_lang.py`` resolves
        to ``src/roam/languages/base.py``.
      * ``..commands.resolve`` from ``src/roam/index/indexer.py``
        resolves to ``src/roam/commands/resolve.py``.

    Both ``.py`` files and package directories (``__init__.py``) count
    as resolved.
    """
    if not dotted.startswith("."):
        return None
    n_dots = len(dotted) - len(dotted.lstrip("."))
    bare = dotted.lstrip(".")
    base = importing_file.parent
    for _ in range(n_dots - 1):
        base = base.parent
    if bare:
        candidate = base.joinpath(*bare.split("."))
    else:
        candidate = base
    py_file = candidate.with_suffix(".py")
    if py_file.exists():
        return py_file
    if candidate.is_dir() and (candidate / "__init__.py").exists():
        return candidate / "__init__.py"
    return None


def _scan_python(conn) -> tuple[list[dict], int]:
    indexed = _indexed_python_modules(conn)
    # W161 — subtree-scoped indexed-modules sets. Test-to-test and
    # benchmark-to-benchmark imports are valid and must resolve, but
    # they're excluded from the default ``indexed`` set so they don't
    # leak into the src-level orphan check. We merge the right subtree
    # set in below based on the importing file's location.
    tests_modules: set[str] | None = None
    benchmarks_modules: set[str] | None = None
    project_root = Path(".").resolve()
    rows = conn.execute("SELECT path FROM files WHERE language = 'python' ORDER BY path").fetchall()
    orphans: list[dict] = []
    files_scanned = 0
    for r in rows:
        rel_path = r[0]
        full = Path(rel_path)
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        files_scanned += 1
        # W161 — choose the per-file indexed set. Lazy: only build the
        # subtree sets if we actually scan a file in that subtree.
        norm_path = rel_path.replace("\\", "/")
        if norm_path.startswith("tests/") or norm_path == "tests":
            if tests_modules is None:
                tests_modules = _indexed_python_subtree_modules(conn, "tests")
            scoped_indexed = indexed | tests_modules
        elif norm_path.startswith("benchmarks/") or norm_path == "benchmarks":
            if benchmarks_modules is None:
                benchmarks_modules = _indexed_python_subtree_modules(conn, "benchmarks")
            scoped_indexed = indexed | benchmarks_modules
        else:
            scoped_indexed = indexed
        # W159: scan a copy with triple-quoted strings + comments masked
        # so prose like "...not visible from any\nimport or call edge..."
        # inside a docstring doesn't produce phantom ``or`` orphans. Line
        # offsets are preserved by the mask so downstream line numbers
        # remain accurate.
        scan_text = _mask_python_strings_and_comments(text)
        # W160 fix 2 — pre-compute optional-import line set per file.
        # AST parses the original (unmasked) source so ``try``/``except``
        # structure is preserved; the cost is bounded and per-file.
        optional_lines = _optional_import_line_set(text)
        for m in _PY_IMPORT_RE.finditer(scan_text):
            line_start = m.start()
            line_end = scan_text.find("\n", line_start)
            line = scan_text[line_start:line_end] if line_end > 0 else scan_text[line_start:]
            if _PY_RELATIVE_PREFIX_RE.match(line):
                continue
            mod = m.group(1) or m.group(2)
            if not mod or mod in scoped_indexed:
                continue
            line_no = scan_text.count("\n", 0, line_start) + 1
            # W160 fix 2 — skip imports inside ``try: ... except ImportError:``.
            if line_no in optional_lines:
                continue
            # W160 fix 3 — resolve relative imports against the importing
            # file. Most ``from .x import y`` lines are already filtered by
            # _PY_RELATIVE_PREFIX_RE, but ``from .pkg.mod`` re-exports and
            # stray ``import .base`` style captures still hit the orphan
            # path. Treat them as RESOLVED when the file actually exists.
            if mod.startswith("."):
                if _resolve_relative_import(mod, full, project_root) is not None:
                    continue
            # W160 fix 1 — pytest auto-discovers conftest. Treat it as resolved.
            if _is_conftest_path(mod, project_root, full):
                continue
            head = mod.split(".", 1)[0]
            if head in scoped_indexed:
                orphans.append(
                    {
                        "language": "python",
                        "file": rel_path,
                        "line": line_no,
                        "module": mod,
                        "kind": "internal_typo",
                        "hint": f"top-level package '{head}' is indexed but '{mod}' is not",
                    }
                )
                continue
            if _is_external_python_package(mod, project_root):
                continue
            orphans.append(
                {
                    "language": "python",
                    "file": rel_path,
                    "line": line_no,
                    "module": mod,
                    "kind": "missing_package",
                    "hint": "neither indexed nor importable; check spelling or install package",
                }
            )
    return orphans, files_scanned


def _scan_javascript(conn) -> tuple[list[dict], int]:
    indexed = _indexed_js_modules(conn)
    ph = ",".join("?" * len(JS_FAMILY_LANGUAGES))
    rows = conn.execute(
        f"SELECT path FROM files WHERE language IN ({ph}) ORDER BY path",
        JS_FAMILY_LANGUAGES,
    ).fetchall()
    orphans: list[dict] = []
    files_scanned = 0
    for r in rows:
        rel_path = (r[0] or "").replace("\\", "/")
        full = Path(rel_path)
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        files_scanned += 1
        # Build the file's directory once for relative resolution.
        importer_dir = "/".join(rel_path.split("/")[:-1])
        for m in _JS_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if not spec:
                continue
            line_start = m.start()
            line_no = text.count("\n", 0, line_start) + 1
            if _is_external_js_package(spec):
                continue  # bare specifier = npm package; trust resolver
            # Relative path resolve.
            target = spec
            if target.startswith("./") or target.startswith("../"):
                pieces = (importer_dir + "/" + target).split("/") if importer_dir else target.split("/")
                stack: list[str] = []
                for p in pieces:
                    if p in ("", "."):
                        continue
                    if p == "..":
                        if stack:
                            stack.pop()
                    else:
                        stack.append(p)
                target = "/".join(stack)
            if target in indexed:
                continue
            # CSS/SCSS/etc. imports resolve to an on-disk asset the indexer
            # doesn't register as a JS module — check the filesystem before
            # flagging so an existing stylesheet isn't a false orphan.
            if _style_asset_resolves(target):
                continue
            orphans.append(
                {
                    "language": "javascript",
                    "file": rel_path,
                    "line": line_no,
                    "module": spec,
                    "kind": "missing_local",
                    "hint": f"resolved path '{target}' not in indexed JS/TS files",
                }
            )
    return orphans, files_scanned


def _scan_go(conn) -> tuple[list[dict], int]:
    indexed = _indexed_go_packages(conn)
    rows = conn.execute("SELECT path FROM files WHERE language = 'go' ORDER BY path").fetchall()
    orphans: list[dict] = []
    files_scanned = 0
    for r in rows:
        rel_path = (r[0] or "").replace("\\", "/")
        full = Path(rel_path)
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        files_scanned += 1
        candidates: list[tuple[int, str]] = []
        # Single-line imports.
        for m in _GO_IMPORT_SINGLE_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            candidates.append((line_no, m.group(1)))
        # Block imports.
        for m in _GO_IMPORT_BLOCK_RE.finditer(text):
            block_offset = m.start(1)
            for inner in _GO_IMPORT_LINE_RE.finditer(m.group(1)):
                line_no = text.count("\n", 0, block_offset + inner.start()) + 1
                candidates.append((line_no, inner.group(1)))
        for line_no, spec in candidates:
            if not spec or spec in indexed:
                continue
            if _is_external_go_package(spec):
                continue
            orphans.append(
                {
                    "language": "go",
                    "file": rel_path,
                    "line": line_no,
                    "module": spec,
                    "kind": "missing_local",
                    "hint": "Go import path not in indexed packages",
                }
            )
    return orphans, files_scanned


_SCANNERS = {
    "python": _scan_python,
    "javascript": _scan_javascript,
    "go": _scan_go,
}


@roam_capability(
    name="orphan-imports",
    category="refactoring",
    summary="List imports that don't resolve to any indexed module / installed package",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command(name="orphan-imports")
@click.option(
    "--lang",
    type=click.Choice(["all", "python", "javascript", "go"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Restrict to a single language scan.",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist findings to .roam/index.db findings registry "
        "(cross-detector queryable via `roam findings list --detector orphan-imports`). "
        "The detector-specific output is unchanged; the registry rows are "
        "the denormalised cross-detector surface. Persisted set ignores "
        "the --lang display filter — every orphan import detected on the "
        "selected languages is mirrored so a downstream filter doesn't "
        "truncate the registry."
    ),
)
@click.pass_context
def orphan_imports(ctx, lang, persist) -> None:
    """List imports that don't resolve to any indexed module / installed package.

    Python (default), JavaScript/TypeScript and Go are supported. Pass
    ``--lang`` to limit the scan.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    ensure_index()
    targets = ["python", "javascript", "go"] if lang.lower() == "all" else [lang.lower()]
    all_orphans: list[dict] = []
    files_scanned = 0

    # W607-CR -- substrate-boundary plumbing for cmd_orphan_imports.
    # ``_run_check_cr`` wraps each substrate helper so an uncaught raise
    # in any one boundary degrades to a sensible empty-floor default
    # AND surfaces a marker in ``_w607cr_warnings_out`` rather than
    # crashing the orphan-imports detector outright (W132 origin per
    # CLAUDE.md detector roster -- part of the original 16 findings-
    # registry detectors; W812 empty-corpus smoke + W814 partial_success
    # fix; W160 conftest + try-except + relative-import filters). Marker
    # family ``orphan_imports_<phase>_failed:<exc_class>:<detail>``.
    # Substrates wrapped:
    #
    #   * scan_python                -- per-language Python orphan scanner
    #                                   (covers W160 conftest + try/except
    #                                   + relative-import filter helpers
    #                                   indirectly via the scanner's call
    #                                   tree)
    #   * scan_javascript            -- per-language JS/TS orphan scanner
    #   * scan_go                    -- per-language Go orphan scanner
    #   * emit_findings              -- W132 findings-registry mirror
    #                                   (sqlite3.OperationalError silent
    #                                   no-op preserved for pre-W89 DB)
    #   * serialize_to_sarif         -- SARIF projection
    #   * derive_distribution        -- R22 wrap + confidence_distribution
    #                                   + verdict_with_high_count
    _w607cr_warnings_out: list[str] = []

    def _run_check_cr(phase, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-CR marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception, surface an
        ``orphan_imports_<phase>_failed:<exc_class>:<detail>`` marker
        via ``_w607cr_warnings_out`` and return *default* -- the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level disclosure
            _w607cr_warnings_out.append(f"orphan_imports_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=not persist) as conn:
        for tgt in targets:
            # W607-CR: per-language scanner substrate. A raise inside one
            # language's scanner (e.g., a malformed regex match on a Go
            # file) degrades to ([], 0) for that language so the OTHER
            # languages still classify correctly and the marker surfaces.
            # Per-language isolation: a Python-scan failure must NOT
            # block the JavaScript and Go scans.
            phase = f"scan_{tgt}"
            scanner = _SCANNERS[tgt]
            result = _run_check_cr(phase, scanner, conn, default=([], 0))
            if result is None:
                result = ([], 0)
            orphans, n = result
            all_orphans.extend(orphans)
            files_scanned += n

        # --- W132: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is the full orphan
        # list for the selected --lang targets — the JSON-mode cap of
        # 200 below is a display truncation, not a persistence one, so
        # the registry stays comprehensive regardless of how a
        # particular invocation slices the view.
        # W607-CR: ``emit_findings`` substrate boundary. The pre-W89
        # schema path (sqlite3.OperationalError on missing ``findings``
        # table) is the EXPECTED degraded path -- the try/except below
        # maintains the W132 silent no-op contract for that case.
        # Generic exceptions surface via the
        # ``orphan_imports_emit_findings_failed:<exc>:<detail>`` marker.
        if persist:
            try:
                _emit_orphan_imports_findings(conn, all_orphans, ORPHAN_IMPORTS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: findings table missing (pre-W89 schema) —
                # degrade gracefully. Surface lineage so a non-expected
                # variant (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_orphan_imports:emit_findings", _exc)
            except Exception as _emit_exc:  # noqa: BLE001 -- W607-CR disclosure
                _w607cr_warnings_out.append(
                    f"orphan_imports_emit_findings_failed:{type(_emit_exc).__name__}:{_emit_exc}"
                )

    verdict = (
        f"OK — no orphan imports across {files_scanned} file(s)"
        if not all_orphans
        else f"{len(all_orphans)} orphan import(s) across {files_scanned} file(s)"
    )

    # --- SARIF output (W1218) ---
    # Branches BEFORE json/text so the pre-existing paths stay
    # byte-identical to pre-W1218. The SARIF projection mirrors the
    # full orphan list (NOT the 200-item display cap applied in
    # json_mode below) so a CI gate sees every detected orphan, not a
    # truncated slice.
    if sarif_mode:
        # W607-CR: ``serialize_to_sarif`` substrate -- a raise in the
        # SARIF writer used to crash the orphan-imports command on the
        # CI integration path; now degrades silently with a marker, and
        # the function returns early (matches pre-W607-CR semantics
        # that SARIF mode short-circuits).
        def _emit_sarif():
            from roam.output.sarif import orphan_imports_to_sarif, write_sarif

            click.echo(write_sarif(orphan_imports_to_sarif(all_orphans)))

        _run_check_cr("serialize_to_sarif", _emit_sarif, default=None)
        return

    if json_mode:
        # R22: wrap each orphan in {value, confidence, reason}.
        # Consumers that previously read `orphans[i]["module"]` must
        # now read `orphans[i]["value"]["module"]` plus
        # `orphans[i]["confidence"]` / `orphans[i]["reason"]`.
        # W607-CR: ``derive_distribution`` substrate -- R22 wrap +
        # distribution computation. Degrades to ([], {}, verdict) so
        # the envelope still emits with empty orphans and an unwrapped
        # verdict.
        def _derive_distribution():
            _triples = wrap_findings(all_orphans[:200], classifier=_orphan_classify)
            _dist = confidence_distribution(_triples)
            _wrapped = verdict_with_high_count(verdict, _dist)
            return (_triples, _dist, _wrapped)

        _derive_result = _run_check_cr(
            "derive_distribution",
            _derive_distribution,
            default=([], {}, verdict),
        )
        if _derive_result is None:
            _derive_result = ([], {}, verdict)
        orphan_triples, distribution, verdict_with_conf = _derive_result

        # W607-CR: mirror substrate markers into BOTH the top-level
        # envelope ``warnings_out`` AND ``summary.warnings_out`` so MCP
        # consumers see disclosure regardless of which surface they
        # read. A non-empty W607-CR bucket flips partial_success so a
        # degraded path is NOT mistaken for a clean populated run
        # (Pattern-2 silent-fallback guard, paired with W812/W814
        # empty-corpus history).
        summary_block = {
            "verdict": verdict_with_conf,
            "count": len(all_orphans),
            "files_scanned": files_scanned,
            "languages": targets,
            "findings_confidence_distribution": distribution,
        }
        envelope_kwargs: dict = {
            "summary": summary_block,
            "orphans": orphan_triples,
        }
        if _w607cr_warnings_out:
            summary_block["warnings_out"] = list(_w607cr_warnings_out)
            summary_block["partial_success"] = True
            envelope_kwargs["warnings_out"] = list(_w607cr_warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "orphan-imports",
                    **envelope_kwargs,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if not all_orphans:
        return
    click.echo()
    click.echo(f"{'File:Line':<55}  {'Lang':<10}  {'Kind':<16}  Module")
    click.echo(f"{'-' * 55}  {'-' * 10}  {'-' * 16}  {'-' * 30}")
    for o in all_orphans[:50]:
        loc_str = f"{o['file']}:{o['line']}"
        click.echo(f"{loc_str:<55}  {o['language']:<10}  {o['kind']:<16}  {o['module']}")
    if len(all_orphans) > 50:
        click.echo()
        click.echo(f"… {len(all_orphans) - 50} more (use --json for the full list)")
