"""Filesystem-based reachability heuristics for SBOM dependency tracing.

The graph-based ``_compute_reachability`` in ``cmd_sbom.py`` walks the indexed
symbol graph. That alone misses 5 systematic false-positive categories:

* **A — CSS side-effect imports** (``primeicons``, ``tailwindcss``)
* **B — Dynamic imports** (``await import("...")``)
* **C — Config-file imports** (``vite.config.ts``, ``tsconfig.json extends``)
* **D — package.json script consumers** (``rimraf`` in npm scripts)
* **E — Peer / loader deps** (``jiti`` for ESLint flat-config, ``@types/*``)

This module supplements the graph-based check with cheap regex scans of:

* ``.css`` / ``.scss`` / ``.sass`` / ``.less`` files (``@import``)
* ``<style>`` blocks in ``.vue`` / ``.svelte`` files
* ``.{ts,tsx,js,jsx,mjs,cjs}`` files (dynamic ``import(...)``)
* JS/TS config files outside ``src/`` (``vite.config.*``, ``eslint.config.*``,
  ``postcss.config.*``, ``tailwind.config.*``, ``cypress.config.*``,
  ``commitlint.config.*``, ``.eslintrc.*``)
* ``tsconfig*.json`` (``extends`` field)
* ``package.json:scripts`` (binary-name cross-reference)
* Known runtime loaders & ``@types/*`` (always-on heuristics)

Output: ``{dep_name: {reachable: bool, sources: [reason strings], confidence: str}}``

``confidence`` is one of:

* ``direct`` — appears in an imported file in the symbol graph
* ``css_import``, ``dynamic_import``, ``config_import`` — heuristic scan hit
* ``script_consumer`` — referenced in package.json scripts
* ``loader`` — known runtime loader / ``@types/*``

Consumers can choose to surface or hide low-confidence reachability claims.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Regex patterns (compiled once)
# ---------------------------------------------------------------------------

# @import "primeicons/primeicons.css" / @import url("foo.css") / @import 'x';
_CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?(?P<q>['"])(?P<spec>[^'"]+)(?P=q)""",
    re.IGNORECASE,
)

# import('pkg') / await import("pkg") / import(`pkg`)
_DYNAMIC_IMPORT_RE = re.compile(r"""(?:await\s+)?import\s*\(\s*(?P<q>['"`])(?P<spec>[^'"`)]+)(?P=q)\s*\)""")

# import X from 'pkg' / import 'pkg' / import * as X from 'pkg'
_STATIC_IMPORT_RE = re.compile(r"""import\s+(?:[\w*{}\s,]+\s+from\s+)?(?P<q>['"])(?P<spec>[^'"]+)(?P=q)""")

# require('pkg') / require("pkg")
_REQUIRE_RE = re.compile(r"""\brequire\s*\(\s*(?P<q>['"])(?P<spec>[^'"]+)(?P=q)\s*\)""")

# <style ...>...</style> blocks in .vue/.svelte (lang-agnostic)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(?P<body>.*?)</style>", re.IGNORECASE | re.DOTALL)

# PHP `use Vendor\Package\...;` / `new Vendor\Package\Foo()`
_PHP_USE_RE = re.compile(r"""\buse\s+([A-Za-z_][\w\\]*)\s*(?:as\s+\w+)?\s*;""")

# Glob patterns the reachability sweep should always cover, regardless of
# any .gitignore / src-only heuristics.
_CONFIG_FILE_PATTERNS: tuple[str, ...] = (
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "vitest.config.ts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.cjs",
    "eslint.config.ts",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc.js",
    ".eslintrc.cjs",
    "postcss.config.ts",
    "postcss.config.js",
    "postcss.config.cjs",
    "postcss.config.mjs",
    "tailwind.config.ts",
    "tailwind.config.js",
    "tailwind.config.cjs",
    "cypress.config.ts",
    "cypress.config.js",
    "commitlint.config.ts",
    "commitlint.config.js",
    "rollup.config.ts",
    "rollup.config.js",
    "rollup.config.mjs",
    "webpack.config.ts",
    "webpack.config.js",
    "nuxt.config.ts",
    "nuxt.config.js",
    "next.config.ts",
    "next.config.js",
    "next.config.mjs",
    "svelte.config.ts",
    "svelte.config.js",
    "astro.config.ts",
    "astro.config.js",
    "astro.config.mjs",
    "playwright.config.ts",
    "playwright.config.js",
    "jest.config.ts",
    "jest.config.js",
    "jest.config.cjs",
    "babel.config.ts",
    "babel.config.js",
    "babel.config.cjs",
    "babel.config.json",
)

# JSON config files. Reading is structural (json.loads), not regex.
_JSON_CONFIG_PATTERNS: tuple[str, ...] = (
    ".eslintrc.json",
    ".babelrc.json",
    ".babelrc",
    "tsconfig.json",
)

# tsconfig variants are open-ended; matched separately by prefix.
_TSCONFIG_PREFIX = "tsconfig"

# Source extensions to walk for static/dynamic imports.
_SOURCE_EXTS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".svelte",
    ".astro",
)

# Style extensions to walk for @import.
_STYLE_EXTS: tuple[str, ...] = (".css", ".scss", ".sass", ".less")

# Common npm binary-name aliases. Map binary -> owning package.
# Add entries as new false positives are confirmed.
_BIN_TO_PACKAGE: dict[str, str] = {
    "run-p": "npm-run-all2",
    "run-s": "npm-run-all2",
    "npm-run-all": "npm-run-all",
    "tsc": "typescript",
    "tsx": "tsx",
    "ts-node": "ts-node",
    "vue-tsc": "vue-tsc",
    "vite": "vite",
    "vitest": "vitest",
    "eslint": "eslint",
    "prettier": "prettier",
    "rimraf": "rimraf",
    "husky": "husky",
    "lint-staged": "lint-staged",
    "concurrently": "concurrently",
    "cross-env": "cross-env",
    "nodemon": "nodemon",
    "webpack": "webpack",
    "rollup": "rollup",
    "jest": "jest",
    "mocha": "mocha",
    "cypress": "cypress",
    "playwright": "@playwright/test",
    "nest": "@nestjs/cli",
    "ng": "@angular/cli",
    "next": "next",
    "nuxt": "nuxt",
    "remix": "remix",
    "astro": "astro",
    "svelte-kit": "@sveltejs/kit",
    "svelte-check": "svelte-check",
    "storybook": "@storybook/cli",
    "tsup": "tsup",
    "esbuild": "esbuild",
    "swc": "@swc/cli",
    "biome": "@biomejs/biome",
    "commitlint": "@commitlint/cli",
    "stylelint": "stylelint",
    "wireit": "wireit",
    "turbo": "turbo",
    "lerna": "lerna",
    "nx": "nx",
    "changeset": "@changesets/cli",
    "patch-package": "patch-package",
}

# Known runtime loaders: their presence is required for TS configs, ESLint
# flat-config TS, etc. If the project has any TS in it, these count as
# reachable when present in devDependencies.
_KNOWN_TS_LOADERS: frozenset[str] = frozenset(
    {
        "jiti",  # ESLint TS flat-config loader
        "ts-node",
        "tsx",
        "esbuild",
        "esbuild-register",
        "@swc/core",
        "@swc/register",
        "babel-loader",
        "swc-loader",
        "ts-loader",
    }
)

# package.json sections that contribute "declared as a dep" status.
_DEP_SECTIONS: tuple[str, ...] = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_root_package(spec: str) -> str:
    """Map an import specifier to its npm package root.

    ``primeicons/primeicons.css`` -> ``primeicons``
    ``@scope/pkg/sub`` -> ``@scope/pkg``
    ``./local`` -> ``""`` (relative — not a dep)
    """
    if not spec:
        return ""
    if spec.startswith(".") or spec.startswith("/"):
        return ""
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _safe_read(path: Path, limit: int = 5_000_000) -> str:
    """Read a text file up to ``limit`` bytes; return ``""`` on any error."""
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _walk_pruned(project_root: Path, skip_dirs: Iterable[str]) -> Iterable[Path]:
    """Yield files under ``project_root``, pruning ``skip_dirs`` during descent.

    Unlike ``Path.rglob('*')`` -- which descends into heavy artifact dirs
    (``.roam`` is ~56K paths on roam-code itself, plus ``.git`` /
    ``node_modules``) and discards them only via a post-hoc
    ``any(part in skip_dirs ...)`` filter -- this prunes those subtrees
    in-place (``dirnames[:] = ...``) so they are never walked or stat'd. It
    yields the same set of files the post-hoc filter produced (the skip names
    never appear among ``project_root``'s own ancestors in practice), so every
    caller stays output-identical while shedding the dominant traversal cost.
    """
    skip = set(skip_dirs)
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        base = Path(dirpath)
        for fn in filenames:
            yield base / fn


def _iter_files(
    project_root: Path,
    exts: Iterable[str],
    *,
    max_files: int = 5000,
    skip_dirs: frozenset[str] = frozenset(
        {"node_modules", ".git", ".roam", "dist", "build", "out", "coverage", ".next", ".nuxt", "__pycache__"}
    ),
) -> list[Path]:
    """Walk ``project_root`` for files with any of ``exts``.

    Skips heavy artifact directories (pruned during descent). Capped at
    ``max_files`` to keep the scan cheap on large monorepos.
    """
    exts_set = {e.lower() for e in exts}
    results: list[Path] = []
    for path in _walk_pruned(project_root, skip_dirs):
        if len(results) >= max_files:
            break
        if path.suffix.lower() in exts_set:
            results.append(path)
    return results


def _iter_named_files(
    project_root: Path,
    names: Iterable[str],
    *,
    max_depth: int = 4,
) -> list[Path]:
    """Find files matching exact names within ``max_depth`` levels.

    Used for config-file discovery: ``vite.config.ts``, ``tsconfig.json``, etc.
    Always scans the root + 1 level, then up to ``max_depth`` for monorepos.
    """
    name_set = {n.lower() for n in names}
    results: list[Path] = []
    root_depth = len(project_root.parts)
    skip_dirs = {"node_modules", ".git", ".roam", "dist", "build", "out", "coverage"}
    for path in _walk_pruned(project_root, skip_dirs):
        depth = len(path.parts) - root_depth
        if depth > max_depth:
            continue
        if path.name.lower() in name_set:
            results.append(path)
    return results


def _find_tsconfigs(project_root: Path, *, max_depth: int = 4) -> list[Path]:
    """Find ``tsconfig*.json`` files."""
    results: list[Path] = []
    root_depth = len(project_root.parts)
    skip_dirs = {"node_modules", ".git", ".roam", "dist", "build", "out"}
    for path in _walk_pruned(project_root, skip_dirs):
        name = path.name
        if not (name.startswith("tsconfig") and name.endswith(".json")):
            continue
        depth = len(path.parts) - root_depth
        if depth > max_depth:
            continue
        results.append(path)
    return results


# ---------------------------------------------------------------------------
# Category A — CSS side-effect imports
# ---------------------------------------------------------------------------


def _scan_css_imports(project_root: Path) -> list[str]:
    """Return import specifiers found in .css/.scss/.sass/.less files
    AND inside ``<style>`` blocks of .vue/.svelte/.astro files.
    """
    specs: list[str] = []

    # Plain stylesheet files
    for path in _iter_files(project_root, _STYLE_EXTS):
        text = _safe_read(path)
        if not text:
            continue
        for m in _CSS_IMPORT_RE.finditer(text):
            specs.append(m.group("spec"))

    # <style> blocks in component frameworks
    for path in _iter_files(project_root, (".vue", ".svelte", ".astro")):
        text = _safe_read(path)
        if not text:
            continue
        for style_m in _STYLE_BLOCK_RE.finditer(text):
            body = style_m.group("body")
            for m in _CSS_IMPORT_RE.finditer(body):
                specs.append(m.group("spec"))

    return specs


# ---------------------------------------------------------------------------
# Category B — Dynamic imports
# ---------------------------------------------------------------------------


def _scan_dynamic_imports(project_root: Path) -> list[str]:
    """Return string-literal specifiers passed to ``import(...)`` calls."""
    specs: list[str] = []
    for path in _iter_files(project_root, _SOURCE_EXTS):
        text = _safe_read(path)
        if not text:
            continue
        for m in _DYNAMIC_IMPORT_RE.finditer(text):
            specs.append(m.group("spec"))
    return specs


# ---------------------------------------------------------------------------
# Category C — Config-file imports
# ---------------------------------------------------------------------------


def _scan_config_imports(project_root: Path) -> list[str]:
    """Return import specifiers found in config files (vite, eslint, postcss,
    tailwind, cypress, commitlint, tsconfig extends, etc.).
    """
    specs: list[str] = []

    # JS/TS configs — static + require + dynamic imports
    js_configs = _iter_named_files(project_root, _CONFIG_FILE_PATTERNS)
    for path in js_configs:
        text = _safe_read(path)
        if not text:
            continue
        for m in _STATIC_IMPORT_RE.finditer(text):
            specs.append(m.group("spec"))
        for m in _REQUIRE_RE.finditer(text):
            specs.append(m.group("spec"))
        for m in _DYNAMIC_IMPORT_RE.finditer(text):
            specs.append(m.group("spec"))

    # tsconfig*.json — "extends" + "plugins" + "compilerOptions.types"
    for path in _find_tsconfigs(project_root):
        text = _safe_read(path)
        if not text:
            continue
        try:
            # tsconfig allows JSONC; strip line comments before parsing
            cleaned = re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.MULTILINE | re.DOTALL)
            # Strip trailing commas
            cleaned = re.sub(r",(\s*[\]}])", r"\1", cleaned)
            data = json.loads(cleaned)
        except (ValueError, TypeError):
            continue
        _collect_tsconfig_refs(data, specs)

    # Other JSON configs (.eslintrc.json, .babelrc*, etc.)
    for path in _iter_named_files(project_root, _JSON_CONFIG_PATTERNS):
        text = _safe_read(path)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        _collect_json_config_refs(data, specs)

    return specs


def _collect_tsconfig_refs(data: object, specs: list[str]) -> None:
    """Walk a parsed tsconfig dict and append package references."""
    if not isinstance(data, dict):
        return
    extends = data.get("extends")
    if isinstance(extends, str):
        specs.append(extends)
    elif isinstance(extends, list):
        for item in extends:
            if isinstance(item, str):
                specs.append(item)

    compiler = data.get("compilerOptions", {})
    if isinstance(compiler, dict):
        types = compiler.get("types", [])
        if isinstance(types, list):
            for t in types:
                if isinstance(t, str):
                    # `types: ["node"]` => `@types/node`
                    specs.append(f"@types/{t}" if not t.startswith("@") else t)
        # plugins — e.g., ts-plugin-vue-language-services
        plugins = compiler.get("plugins", [])
        if isinstance(plugins, list):
            for p in plugins:
                if isinstance(p, dict) and isinstance(p.get("name"), str):
                    specs.append(p["name"])
                elif isinstance(p, str):
                    specs.append(p)


def _collect_json_config_refs(data: object, specs: list[str]) -> None:
    """Walk a parsed JSON config (eslint/babel/etc.) for package refs.

    Conservative: collect ``extends``, ``plugins``, ``presets``, ``parser``.
    """
    if not isinstance(data, dict):
        return
    for key in ("extends", "plugins", "presets", "parser", "parserOptions"):
        val = data.get(key)
        if isinstance(val, str):
            specs.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    specs.append(item)
                elif isinstance(item, list) and item and isinstance(item[0], str):
                    # ["@babel/preset-env", {...}]
                    specs.append(item[0])
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    specs.append(item["name"])


# ---------------------------------------------------------------------------
# Category D — package.json script consumers
# ---------------------------------------------------------------------------

# Tokens that should never be flagged as a "binary name" in scripts.
_SCRIPT_SHELL_TOKENS: frozenset[str] = frozenset(
    {
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bun",
        "node",
        "&&",
        "||",
        ";",
        "|",
        "&",
        "if",
        "then",
        "else",
        "fi",
        "for",
        "do",
        "done",
        "cd",
        "mkdir",
        "rm",
        "cp",
        "mv",
        "echo",
        "cat",
        "exit",
        "true",
        "false",
    }
)


def _scan_script_consumers(project_root: Path, declared_deps: set[str]) -> dict[str, list[str]]:
    """Walk every ``package.json:scripts`` and return ``{dep_name: [reasons]}``.

    A token in a script command counts as a consumer if it matches:

    * a literal package name in ``declared_deps``, or
    * an alias in ``_BIN_TO_PACKAGE`` whose mapped package is in ``declared_deps``.
    """
    result: dict[str, list[str]] = {}
    for path in _iter_named_files(project_root, ("package.json",)):
        text = _safe_read(path)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if not isinstance(scripts, dict):
            continue
        for script_name, cmd in scripts.items():
            if not isinstance(cmd, str):
                continue
            # Strip flag tokens (--foo) and arg substitutions ($1, ${X})
            # before tokenizing — they're never package names.
            tokens = re.split(r"\s+", cmd.strip())
            for tok in tokens:
                tok = tok.strip()
                if not tok or tok.startswith("-") or tok.startswith("$"):
                    continue
                if tok in _SCRIPT_SHELL_TOKENS:
                    continue
                # Strip path prefixes (node_modules/.bin/eslint -> eslint)
                if "/" in tok:
                    base = tok.split("/")[-1]
                else:
                    base = tok
                # Direct hit
                pkg = None
                if base in declared_deps:
                    pkg = base
                elif base in _BIN_TO_PACKAGE and _BIN_TO_PACKAGE[base] in declared_deps:
                    pkg = _BIN_TO_PACKAGE[base]
                # `npm run X` / `npx X` / `pnpm dlx X` — already filtered above
                if pkg:
                    reason = f"package.json:scripts.{script_name}"
                    result.setdefault(pkg, []).append(reason)
    return result


# ---------------------------------------------------------------------------
# Category E — Loader / peer deps
# ---------------------------------------------------------------------------


def _scan_loader_deps(project_root: Path, declared_deps: set[str]) -> dict[str, list[str]]:
    """Mark known runtime loaders as reachable when the project has TS code.

    * Any ``@types/*`` declared in deps is reachable (TS compiler loads them).
    * Known TS loaders (``jiti``, ``ts-node``, ``tsx``, etc.) are reachable
      when the project contains ``.ts``/``.tsx`` files OR a TS config file.
    """
    result: dict[str, list[str]] = {}

    # @types/* are always loaded by the TS compiler
    for dep in declared_deps:
        if dep.startswith("@types/"):
            result.setdefault(dep, []).append("@types loaded by TypeScript compiler")

    # Detect TS presence: any .ts/.tsx in project (cheap rglob check)
    has_ts = False
    for path in _walk_pruned(project_root, ("node_modules", ".roam")):
        if path.suffix == ".ts":
            has_ts = True
            break
    if not has_ts:
        # Fall back: check for any TS config
        for cfg in ("tsconfig.json", "eslint.config.ts", "vite.config.ts"):
            if (project_root / cfg).exists():
                has_ts = True
                break

    if has_ts:
        for loader in _KNOWN_TS_LOADERS:
            if loader in declared_deps:
                result.setdefault(loader, []).append("known TS loader (project uses TypeScript)")

    return result


# ---------------------------------------------------------------------------
# PHP composer.json
# ---------------------------------------------------------------------------


def _discover_composer_json(project_root: Path) -> list[Path]:
    """Return composer.json files at root + 1-deep subdirs."""
    found: list[Path] = []
    root_cj = project_root / "composer.json"
    if root_cj.is_file():
        found.append(root_cj)
    for sub in project_root.iterdir() if project_root.is_dir() else []:
        if not sub.is_dir():
            continue
        if sub.name in {"node_modules", ".git", ".roam", "vendor", "dist", "build"}:
            continue
        cj = sub / "composer.json"
        if cj.is_file():
            found.append(cj)
    return found


def parse_composer_json(path: Path) -> list[tuple[str, str, bool]]:
    """Parse a composer.json into a list of ``(name, version_spec, is_dev)``.

    Each entry corresponds to a key under ``require`` or ``require-dev``.
    """
    text = _safe_read(path)
    if not text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    entries: list[tuple[str, str, bool]] = []
    for section, is_dev in (("require", False), ("require-dev", True)):
        block = data.get(section, {})
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if not isinstance(name, str):
                continue
            # Skip the "php" pseudo-requirement and ext-* platform packages
            if name.lower() == "php" or name.lower().startswith("ext-"):
                continue
            spec_str = spec if isinstance(spec, str) else ""
            entries.append((name, spec_str, is_dev))
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_filesystem_reachability(
    project_root: Path,
    declared_deps: list[str],
) -> dict[str, dict]:
    """Return reachability info for each declared dep based on filesystem scan.

    For each dep, output:

    .. code-block:: python

        {
            "reachable": bool,
            "sources": [reason strings],
            "confidence": "css_import" | "dynamic_import" | "config_import" |
                          "script_consumer" | "loader" | "indirect",
        }

    Confidence is set to the *highest* trustworthiness source that flagged
    the dep. Order (most to least): ``config_import`` > ``script_consumer``
    > ``loader`` > ``css_import`` > ``dynamic_import`` > ``indirect``.

    ``reachable`` is True iff any source was found.
    """
    declared_set = {d for d in declared_deps if d}
    out: dict[str, dict] = {dep: {"reachable": False, "sources": [], "confidence": "indirect"} for dep in declared_deps}

    # Confidence priority. Higher number = stronger evidence of real use.
    priority = {
        "indirect": 0,
        "dynamic_import": 1,
        "css_import": 2,
        "loader": 3,
        "script_consumer": 4,
        "config_import": 5,
    }

    def _record(dep: str, reason: str, confidence: str) -> None:
        info = out.get(dep)
        if info is None:
            return
        info["reachable"] = True
        if reason not in info["sources"]:
            info["sources"].append(reason)
        if priority.get(confidence, 0) > priority.get(info["confidence"], 0):
            info["confidence"] = confidence

    # Category A — CSS imports
    for spec in _scan_css_imports(project_root):
        pkg = _spec_root_package(spec)
        if pkg and pkg in declared_set:
            _record(pkg, f"css @import {spec!r}", "css_import")

    # Category B — Dynamic imports
    for spec in _scan_dynamic_imports(project_root):
        pkg = _spec_root_package(spec)
        if pkg and pkg in declared_set:
            _record(pkg, f"dynamic import({spec!r})", "dynamic_import")

    # Category C — Config-file imports
    for spec in _scan_config_imports(project_root):
        pkg = _spec_root_package(spec)
        if pkg and pkg in declared_set:
            _record(pkg, f"config import {spec!r}", "config_import")

    # Category D — package.json scripts
    script_hits = _scan_script_consumers(project_root, declared_set)
    for dep, reasons in script_hits.items():
        for r in reasons:
            _record(dep, r, "script_consumer")

    # Category E — Loaders / @types
    loader_hits = _scan_loader_deps(project_root, declared_set)
    for dep, reasons in loader_hits.items():
        for r in reasons:
            _record(dep, r, "loader")

    return out


def merge_reachability(
    graph: dict[str, dict] | None,
    fs: dict[str, dict] | None,
) -> dict[str, dict]:
    """Merge graph-based + filesystem-based reachability into a unified dict.

    Each output entry has:

    * ``reachable``: ``True`` if either source said so
    * ``entry_points``: union of graph entries
    * ``matched_symbols``: union of graph matches
    * ``sources``: filesystem reasons
    * ``confidence``: ``direct`` if graph reachable, else fs confidence
    """
    keys: set[str] = set()
    if graph:
        keys.update(graph.keys())
    if fs:
        keys.update(fs.keys())

    merged: dict[str, dict] = {}
    for key in keys:
        g = (graph or {}).get(key, {})
        f = (fs or {}).get(key, {})
        graph_reachable = bool(g.get("reachable"))
        fs_reachable = bool(f.get("reachable"))
        entry: dict = {
            "reachable": graph_reachable or fs_reachable,
            "entry_points": list(g.get("entry_points", [])),
            "matched_symbols": list(g.get("matched_symbols", [])),
            "sources": list(f.get("sources", [])),
            "confidence": "direct" if graph_reachable else f.get("confidence", "indirect"),
        }
        merged[key] = entry
    return merged
