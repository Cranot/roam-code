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

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.capability import roam_capability
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

    return make_finding_id(
        "orphan-imports", language, language, file_path, module, int(line or 0)
    )


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
            f"orphan-import ({kind}): {language} module {module!r} "
            f"in {file_path}:{line} — {hint or 'no resolution'}"
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


def _is_external_python_package(module: str) -> bool:
    head = module.split(".", 1)[0]
    if head in sys.builtin_module_names:
        return True
    try:
        return importlib.util.find_spec(head) is not None
    except Exception:
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
            return any(
                isinstance(e, ast.Name) and e.id in _OPTIONAL_NAMES
                for e in h.type.elts
            )
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


def _resolve_relative_import(
    dotted: str, importing_file: Path, project_root: Path
) -> Path | None:
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
            if not mod or mod in indexed:
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
            if head in indexed:
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
            if _is_external_python_package(mod):
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
    ensure_index()
    targets = ["python", "javascript", "go"] if lang.lower() == "all" else [lang.lower()]
    all_orphans: list[dict] = []
    files_scanned = 0
    with open_db(readonly=not persist) as conn:
        for tgt in targets:
            scanner = _SCANNERS[tgt]
            orphans, n = scanner(conn)
            all_orphans.extend(orphans)
            files_scanned += n

        # --- W132: mirror into the central findings registry ---
        # Runs ONLY with --persist. The persisted set is the full orphan
        # list for the selected --lang targets — the JSON-mode cap of
        # 200 below is a display truncation, not a persistence one, so
        # the registry stays comprehensive regardless of how a
        # particular invocation slices the view.
        if persist:
            try:
                _emit_orphan_imports_findings(
                    conn, all_orphans, ORPHAN_IMPORTS_DETECTOR_VERSION
                )
                conn.commit()
            except sqlite3.OperationalError:
                # findings table missing (pre-W89 schema) — degrade gracefully.
                pass

    verdict = (
        f"OK — no orphan imports across {files_scanned} file(s)"
        if not all_orphans
        else f"{len(all_orphans)} orphan import(s) across {files_scanned} file(s)"
    )

    if json_mode:
        # R22: wrap each orphan in {value, confidence, reason}.
        # Consumers that previously read `orphans[i]["module"]` must
        # now read `orphans[i]["value"]["module"]` plus
        # `orphans[i]["confidence"]` / `orphans[i]["reason"]`.
        orphan_triples = wrap_findings(all_orphans[:200], classifier=_orphan_classify)
        distribution = confidence_distribution(orphan_triples)
        verdict_with_conf = verdict_with_high_count(verdict, distribution)
        click.echo(
            to_json(
                json_envelope(
                    "orphan-imports",
                    summary={
                        "verdict": verdict_with_conf,
                        "count": len(all_orphans),
                        "files_scanned": files_scanned,
                        "languages": targets,
                        "findings_confidence_distribution": distribution,
                    },
                    orphans=orphan_triples,
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
