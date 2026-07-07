"""F9 — library public-surface detection (deterministic, cheap, static).

A dead-code / speculative-generality verdict of "no internal consumers ⇒ safe
to delete" is *wrong* for an export on a distributable library's **public
surface**: external code imports it, and in Python dynamic dispatch +
subclassing consume it invisibly to static call-graph analysis. Shipping
"safely delete ``HTTPAdapter.init_poolmanager``" to the requests team is
credibility death (D1b stranger battery, ``dev/STRANGER-TP-TEST.md``).

This module answers two questions with no LLM and no network:

1. ``detect_library(project_root)`` — is this repo a distributable library, and
   what is its public import surface?
2. ``LibrarySurface.is_external_facing(name, qualified_name, file_path)`` — is a
   given symbol on that public surface?

Detectors (``dead``, ``smells``) use the answer to CAP confidence and label the
finding "external-facing" rather than emit a delete recommendation.

The design is intentionally conservative: when the repo is not clearly a
library, ``is_library`` is ``False`` and every ``is_external_facing`` call
returns ``False`` — detectors behave exactly as before. Any I/O or parse error
degrades to "not a library".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Node.js built-in module names (bare + ``node:`` prefixed both resolve). Kept
# here next to the library-surface logic because F11 (verify-imports JS
# resolution) consumes the same set.
NODE_BUILTINS: frozenset[str] = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }
)


def _is_public_name(name: str | None) -> bool:
    """Python/JS convention: a leading underscore marks a private name."""
    if not name:
        return False
    return not name.startswith("_")


@dataclass(frozen=True)
class LibrarySurface:
    """Resolved public-surface facts for one repo."""

    is_library: bool = False
    #: (prefix_dir, strip_prefix) pairs for Python packages, e.g.
    #: ("src/requests", "src/") or ("fastapi", "").
    py_pkg_prefixes: tuple[tuple[str, str], ...] = ()
    #: JS names exported from the package main entry / ``exports`` map.
    js_export_names: frozenset[str] = frozenset()
    #: Files that DEFINE the JS exports (so a same-named private symbol in an
    #: unrelated file is not mistaken for the public export).
    js_export_files: frozenset[str] = frozenset()

    def is_external_facing(self, name: str, qualified_name: str | None, file_path: str | None) -> bool:
        """True when the symbol is reachable on the library's public surface."""
        if not self.is_library or not name or not file_path:
            return False
        norm = file_path.replace("\\", "/").lstrip("./")
        # --- Python: public symbol in a public module of the package ---------
        for prefix_dir, strip in self.py_pkg_prefixes:
            if norm == prefix_dir or norm.startswith(prefix_dir + "/"):
                if not norm.endswith((".py", ".pyi")):
                    return False
                rel = norm[len(strip) :] if strip and norm.startswith(strip) else norm
                segments = [s for s in rel.split("/") if s]
                if not segments:
                    return False
                *dirs, filename = segments
                # Every package directory must be public (no ``_internal/``).
                if any(not _is_public_name(d) for d in dirs):
                    return False
                stem = re.sub(r"\.pyi?$", "", filename)
                if stem != "__init__" and not _is_public_name(stem):
                    return False
                # Symbol + (for a method) its owning class must be public.
                parts = (qualified_name or name).split(".")
                if all(_is_public_name(p) for p in parts):
                    return True
                # Fall back to the bare name (qualified_name may carry module).
                return _is_public_name(name)
        # --- JS: name is on the package's declared export surface ------------
        if name in self.js_export_names and (not self.js_export_files or norm in self.js_export_files):
            return True
        return False


_EXPORTS_ASSIGN_RE = re.compile(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=")
_MODULE_EXPORTS_REQUIRE_RE = re.compile(r"""module\.exports\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""")
_PY_NAME_RE = re.compile(r"""^\s*name\s*=\s*['"]([^'"]+)['"]""", re.MULTILINE)


def _resolve_js_file(root: Path, rel: str) -> Path | None:
    """Resolve a JS require/main target to a real file under *root*."""
    base = (root / rel).resolve()
    candidates = [base]
    if base.suffix == "":
        candidates += [base.with_suffix(ext) for ext in (".js", ".cjs", ".mjs")]
        candidates.append(base / "index.js")
        candidates.append(base / "index.cjs")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _collect_js_exports(root: Path, main_rel: str) -> tuple[frozenset[str], frozenset[str]]:
    """Read the package main entry (following one ``module.exports = require``
    redirect) and collect ``exports.NAME`` assignment names + the files that
    define them (paths relative to *root*, forward-slashed)."""
    names: set[str] = set()
    files: set[str] = set()
    seen: set[Path] = set()
    to_read = []
    entry = _resolve_js_file(root, main_rel)
    if entry is not None:
        to_read.append(entry)
    for _ in range(3):  # bounded redirect chain
        if not to_read:
            break
        f = to_read.pop()
        if f in seen:
            continue
        seen.add(f)
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        redirect = _MODULE_EXPORTS_REQUIRE_RE.search(text)
        found = _EXPORTS_ASSIGN_RE.findall(text)
        if found:
            rel = f.relative_to(root).as_posix()
            files.add(rel)
            names.update(found)
        if redirect and redirect.group(1).startswith("."):
            target = _resolve_js_file(f.parent, redirect.group(1))
            if target is not None:
                to_read.append(target)
    return frozenset(names), frozenset(files)


def detect_library(project_root) -> LibrarySurface:
    """Detect whether *project_root* is a distributable library + its surface.

    Deterministic reads of ``pyproject.toml`` / ``setup.py`` / ``setup.cfg``
    (Python) and ``package.json`` (JS). Never raises — any error yields a
    non-library surface.
    """
    try:
        root = Path(project_root)
    except (TypeError, ValueError):
        return LibrarySurface()

    is_library = False
    py_prefixes: list[tuple[str, str]] = []
    js_names: frozenset[str] = frozenset()
    js_files: frozenset[str] = frozenset()

    # --- Python -------------------------------------------------------------
    try:
        pyproject = root / "pyproject.toml"
        py_names: list[str] = []
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\[project\]", text, re.MULTILINE) or re.search(r"^\[tool\.poetry\]", text, re.MULTILINE):
                is_library = True
                py_names = _PY_NAME_RE.findall(text)
        if (root / "setup.py").is_file() or (root / "setup.cfg").is_file():
            is_library = True
        # Resolve package import-roots from the declared name(s).
        for nm in py_names:
            pkg = nm.replace("-", "_")
            for prefix_dir, strip in ((pkg, ""), (f"src/{pkg}", "src/")):
                if (root / prefix_dir / "__init__.py").is_file():
                    py_prefixes.append((prefix_dir, strip))
        # Fallback: a src/ layout with package __init__.py files.
        if is_library and not py_prefixes:
            for cand in (root / "src").glob("*/__init__.py"):
                py_prefixes.append((f"src/{cand.parent.name}", "src/"))
            if not py_prefixes:
                for cand in root.glob("*/__init__.py"):
                    d = cand.parent.name
                    if d not in ("tests", "test", "docs", "examples", "build"):
                        py_prefixes.append((d, ""))
    except OSError as exc:
        from roam.observability import log_swallowed

        log_swallowed("library_surface:python_detect", exc)

    # --- JS -----------------------------------------------------------------
    try:
        pkg_json = root / "package.json"
        if pkg_json.is_file():
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("private") is not True and data.get("name"):
                is_library = True
                main_rel = data.get("main") or data.get("module") or "index.js"
                if isinstance(main_rel, str):
                    js_names, js_files = _collect_js_exports(root, main_rel)
                # Names declared directly in an ``exports`` map.
                exports_field = data.get("exports")
                extra: set[str] = set(js_names)
                if isinstance(exports_field, dict):
                    for k in exports_field:
                        key = k.lstrip("./")
                        if key and key != "." and "/" not in key:
                            extra.add(key)
                js_names = frozenset(extra)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        from roam.observability import log_swallowed

        log_swallowed("library_surface:js_detect", exc)

    return LibrarySurface(
        is_library=is_library,
        py_pkg_prefixes=tuple(py_prefixes),
        js_export_names=js_names,
        js_export_files=js_files,
    )
