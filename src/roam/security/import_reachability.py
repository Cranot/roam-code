"""Import-specifier reachability: which declared packages are ACTUALLY imported.

The CVE→reachable-code matcher must answer a concrete buyer question: *is this
vulnerable third-party package imported / used anywhere in this repo, and where?*
roam's symbol graph cannot answer it. An external module gets **no symbol** (the
indexer only records first-party definitions) and **no import edge** (edge
resolution requires a resolved first-party target, so ``import requests`` is
dropped). The result — measured — is that ``match_vuln_to_symbols`` keys a CVE's
package name against *first-party symbol names* and therefore returns nothing for
real dependencies, or a meaningless coincidence when a local variable happens to
share the package's name.

This module fills that gap with a filesystem scan of import specifiers, mapped to
their package root and normalized so a scanner report's *distribution* name
(``PyYAML``) matches the source *import* (``import yaml``). It is deliberately
independent of the symbol index: it works on a fresh checkout, needs no ``roam
index`` first, and cannot be fooled by variable naming.

Scope: Python (``import``/``from ... import``) and JS/TS
(``import ... from``/``require()``/dynamic ``import()``/``export ... from``).
PHP/composer namespace reachability already lives in ``sbom_reachability`` and is
matched there.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from roam.security.sbom_reachability import _spec_root_package

# Directories that never contain first-party source but are heavy to walk.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        ".roam",
        ".hg",
        ".svn",
        "dist",
        "build",
        "out",
        "coverage",
        ".next",
        ".nuxt",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "vendor",
    }
)

_PY_EXTS: frozenset[str] = frozenset({".py", ".pyi"})
_JS_EXTS: frozenset[str] = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"})

# Import name → PyPI distribution name, for the common cases where they diverge.
# A CVE/audit report names the *distribution*; source names the *import*. We
# register both forms so either side of the query matches. Conservative on
# purpose — only well-known, unambiguous divergences.
_IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "pil": "pillow",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "jwt": "pyjwt",
    "serial": "pyserial",
    "openssl": "pyopenssl",
    "crypto": "pycryptodome",
    "magic": "python-magic",
    "usb": "pyusb",
    "attr": "attrs",
    # NOTE: no "google" entry on purpose — ``google`` is a namespace package
    # spanning many distributions (protobuf, googleapis-*, ...); mapping it to
    # any single one would fabricate reachability evidence.
    "slugify": "python-slugify",
    "memcache": "python-memcached",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "ruamel": "ruamel-yaml",
    "zmq": "pyzmq",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "gi": "pygobject",
}


class ImportSite(NamedTuple):
    """A single place a package is imported."""

    file: str  # path relative to project_root, POSIX-style
    line: int
    specifier: str  # the raw import specifier / module path as written


def _norm_keys(name: str) -> set[str]:
    """Return the set of normalized keys a package name should match under.

    Handles the PyPI ``_``/``-`` equivalence and lower-casing (both ecosystems),
    keeps npm scoped names (``@scope/pkg``) intact, and folds in the known
    import↔distribution aliases so ``yaml`` and ``pyyaml`` collide.
    """
    n = (name or "").strip().lower()
    if not n:
        return set()
    keys = {n, n.replace("_", "-"), n.replace("-", "_")}
    alias = _IMPORT_TO_DISTRIBUTION.get(n)
    if alias:
        keys.add(alias)
        keys.add(alias.replace("-", "_"))
    return keys


class ImportReachability:
    """Index of ``normalized package key -> [ImportSite, ...]`` for a project."""

    def __init__(self) -> None:
        self._by_key: dict[str, list[ImportSite]] = {}
        # Preserve insertion order of distinct (file,line,root) so callers get
        # stable, deterministic evidence.
        self._seen: set[tuple[str, int, str]] = set()

    def _add(self, root: str, site: ImportSite) -> None:
        dedup = (site.file, site.line, root)
        if dedup in self._seen:
            return
        self._seen.add(dedup)
        for key in _norm_keys(root):
            self._by_key.setdefault(key, []).append(site)

    def sites_for(self, package_name: str) -> list[ImportSite]:
        """Return the import sites for ``package_name`` (empty if not imported)."""
        out: list[ImportSite] = []
        seen: set[tuple[str, int, str]] = set()
        for key in _norm_keys(package_name):
            for site in self._by_key.get(key, ()):  # noqa: SIM118
                sig = (site.file, site.line, site.specifier)
                if sig not in seen:
                    seen.add(sig)
                    out.append(site)
        out.sort(key=lambda s: (s.file, s.line))
        return out

    def is_reachable(self, package_name: str) -> bool:
        return bool(self.sites_for(package_name))

    def packages(self) -> set[str]:
        return set(self._by_key.keys())

    def __len__(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# Language scanners
# ---------------------------------------------------------------------------


def _iter_source_files(project_root: Path, exts: frozenset[str], max_files: int) -> Iterator[Path]:
    """Yield source files with ``exts`` under ``project_root``, pruning heavy dirs."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        base = Path(dirpath)
        for fn in filenames:
            if Path(fn).suffix.lower() in exts:
                if count >= max_files:
                    return
                count += 1
                yield base / fn


def _read_text(path: Path, limit: int = 3_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_party_python_roots(project_root: Path) -> set[str]:
    """Return unambiguous top-level Python module/package names in the repo."""
    roots: set[str] = set()
    for source_root in (project_root, project_root / "src"):
        if not source_root.is_dir():
            continue
        try:
            children = list(source_root.iterdir())
        except OSError:
            continue
        for path in children:
            if path.is_file() and path.suffix.lower() in _PY_EXTS:
                roots.add(path.stem)
            elif path.is_dir() and (path / "__init__.py").is_file():
                roots.add(path.name)
    return roots


def _python_import_roots(text: str) -> Iterable[tuple[str, int, str]]:
    """Yield ``(root_module, line, specifier)`` for third-party Python imports.

    Uses ``ast`` for correctness; on a syntax error falls back to a line regex so
    a single un-parseable file still contributes its imports. Excludes the
    standard library and relative (``from . import x``) imports.
    """
    stdlib = getattr(sys, "stdlib_module_names", frozenset())

    def _emit(mod: str, line: int) -> tuple[str, int, str] | None:
        if not mod:
            return None
        root = mod.split(".")[0]
        if not root or root == "__future__" or root in stdlib:
            return None
        return (root, line, mod)

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        for i, raw in enumerate(text.splitlines(), start=1):
            m = re.match(r"\s*import\s+([\w.]+)", raw)
            if m:
                r = _emit(m.group(1), i)
                if r:
                    yield r
                continue
            m = re.match(r"\s*from\s+([\w.]+)\s+import\b", raw)
            if m and not m.group(1).startswith("."):
                r = _emit(m.group(1), i)
                if r:
                    yield r
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                r = _emit(alias.name, getattr(node, "lineno", 0))
                if r:
                    yield r
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import (first-party); module None is bare
            # ``from . import x``.
            if node.level == 0 and node.module:
                r = _emit(node.module, getattr(node, "lineno", 0))
                if r:
                    yield r


# import ... from '<spec>' | export ... from '<spec>' | import '<spec>'
_JS_FROM_RE = re.compile(r"""(?:import|export)\b[^;'"]*?\bfrom\s*['"]([^'"]+)['"]""")
_JS_BARE_RE = re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""")
_JS_REQUIRE_RE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_DYNIMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def _js_import_roots(text: str) -> Iterable[tuple[str, int, str]]:
    """Yield ``(package_root, line, specifier)`` for JS/TS imports.

    Scans line-by-line (so multi-line ``import { ... } from 'x'`` still yields
    on the ``from 'x'`` line) across the four common import forms. Relative
    specifiers map to an empty root via ``_spec_root_package`` and are skipped.
    """
    for i, raw in enumerate(text.splitlines(), start=1):
        for rx in (_JS_FROM_RE, _JS_BARE_RE, _JS_REQUIRE_RE, _JS_DYNIMPORT_RE):
            for m in rx.finditer(raw):
                spec = m.group(1)
                root = _spec_root_package(spec)
                if root:
                    yield (root, i, spec)


def scan_import_reachability(project_root: str | Path, *, max_files: int = 6000) -> ImportReachability:
    """Scan ``project_root`` for imported third-party packages.

    Returns an :class:`ImportReachability` mapping normalized package names to the
    concrete files/lines that import them. Never raises on a single bad file — a
    scan error degrades that file to "no imports", not the whole result.
    """
    root = Path(project_root)
    reach = ImportReachability()
    if not root.exists():
        return reach

    first_party_python_roots = _first_party_python_roots(root)
    for path in _iter_source_files(root, _PY_EXTS, max_files):
        text = _read_text(path)
        if not text:
            continue
        rel = _rel(path, root)
        for pkg_root, line, spec in _python_import_roots(text):
            if pkg_root in first_party_python_roots:
                continue
            reach._add(pkg_root, ImportSite(rel, line, spec))

    for path in _iter_source_files(root, _JS_EXTS, max_files):
        text = _read_text(path)
        if not text:
            continue
        rel = _rel(path, root)
        for pkg_root, line, spec in _js_import_roots(text):
            reach._add(pkg_root, ImportSite(rel, line, spec))

    return reach


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
