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

import importlib.util
import re
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

_PY_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import\s+)", re.MULTILINE)
_PY_RELATIVE_PREFIX_RE = re.compile(r"^\s*from\s+\.")

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


def _scan_python(conn) -> tuple[list[dict], int]:
    indexed = _indexed_python_modules(conn)
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
        for m in _PY_IMPORT_RE.finditer(text):
            line_start = m.start()
            line_end = text.find("\n", line_start)
            line = text[line_start:line_end] if line_end > 0 else text[line_start:]
            if _PY_RELATIVE_PREFIX_RE.match(line):
                continue
            mod = m.group(1) or m.group(2)
            if not mod or mod in indexed:
                continue
            head = mod.split(".", 1)[0]
            line_no = text.count("\n", 0, line_start) + 1
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
@click.pass_context
def orphan_imports(ctx, lang) -> None:
    """List imports that don't resolve to any indexed module / installed package.

    Python (default), JavaScript/TypeScript and Go are supported. Pass
    ``--lang`` to limit the scan.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    targets = ["python", "javascript", "go"] if lang.lower() == "all" else [lang.lower()]
    all_orphans: list[dict] = []
    files_scanned = 0
    with open_db(readonly=True) as conn:
        for tgt in targets:
            scanner = _SCANNERS[tgt]
            orphans, n = scanner(conn)
            all_orphans.extend(orphans)
            files_scanned += n

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
