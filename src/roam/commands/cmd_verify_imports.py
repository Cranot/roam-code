"""Verify import statements against the indexed symbol table (hallucination firewall).

W1229: SARIF is deliberately surfaced via the global ``--sarif`` flag.
cmd_verify_imports emits per-import rows (each carrying ``file`` / ``line``
/ ``name`` / ``status`` / ``suggestions``) which the
:func:`roam.output.sarif.verify_imports_to_sarif` projection maps onto two
closed-enum rule ids — ``invalid-import`` (unresolved with fuzzy-match
candidates; warning band) and ``hallucination-import`` (unresolved with no
candidates; error band — the canonical hallucination-firewall signal for
LLM-generated code). See W1229 audit (Wave 15) + the SHIP path in
:mod:`tests.test_sarif_disclosure_coverage` (cmd_verify_imports removed
from ``_KNOWN_MISSING``).
"""

from __future__ import annotations

import functools
import os
import re
import sqlite3
import sys
from collections import defaultdict

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.db.edge_kinds import import_in_clause
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Python stdlib module names (for filtering false positives)
# ---------------------------------------------------------------------------

# sys.stdlib_module_names ships in Python 3.10+; pyproject.toml pins
# requires-python = ">=3.10", so this attribute is always present.
_PYTHON_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)


def _is_stdlib_module(name: str) -> bool:
    """Return True if *name* is a Python stdlib module (top-level check)."""
    top = name.split(".")[0]
    return top in _PYTHON_STDLIB


# ---------------------------------------------------------------------------
# Declared third-party dependencies (for filtering false positives)
# ---------------------------------------------------------------------------

# Distribution name -> import name, for the common mismatches. 10 entries —
# the high-frequency offenders; everything else assumes dist == import name
# after lowercasing and dash->underscore.
_DIST_TO_IMPORT_ALIASES: dict[str, str] = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "python-dateutil": "dateutil",
    "opencv-python": "cv2",
    "protobuf": "google",
    "msgpack-python": "msgpack",
    "attrs": "attr",
    "pyjwt": "jwt",
}

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _import_name_for_requirement(req: str) -> str | None:
    """Map one requirement string to its top-level import name (or None)."""
    m = _REQ_NAME_RE.match(req)
    if not m:
        return None
    dist = m.group(1).lower()
    return _DIST_TO_IMPORT_ALIASES.get(dist, dist.replace("-", "_"))


def _load_toml(path: str) -> dict:
    """Load a TOML file via tomllib (3.11+) or the tomli backport (3.10)."""
    try:
        import tomllib
    except ImportError:  # Python 3.10 — tomli backport (a dependency)
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _extract_pyproject_deps(proj: dict) -> list[str]:
    """Return all requirement strings from a pyproject [project] section."""
    reqs = list(proj.get("dependencies") or [])
    for group in (proj.get("optional-dependencies") or {}).values():
        reqs.extend(group or [])
    return [str(r) for r in reqs]


def _pyproject_requirements(project_root: str) -> list[str]:
    """Every requirement string from pyproject [project] dependencies +
    all optional-dependency groups. Empty list when absent/broken."""
    pyproject = os.path.join(project_root, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return []
    try:
        data = _load_toml(pyproject)
        return _extract_pyproject_deps(data.get("project") or {})
    except Exception as exc:  # noqa: BLE001 — a broken pyproject must not kill the scan
        from roam.observability import log_swallowed

        log_swallowed("verify_imports.declared_deps.pyproject", exc)
        return []


def _parse_req_file(path: str) -> list[str]:
    """Return non-comment, non-flag lines from one requirements*.txt."""
    reqs: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                reqs.append(line)
    return reqs


def _requirements_txt_requirements(project_root: str) -> list[str]:
    """Requirement strings from every requirements*.txt at the root."""
    reqs: list[str] = []
    try:
        import glob as _glob

        for req_file in _glob.glob(os.path.join(project_root, "requirements*.txt")):
            reqs.extend(_parse_req_file(req_file))
    except OSError as exc:
        from roam.observability import log_swallowed

        log_swallowed("verify_imports.declared_deps.requirements", exc)
    return reqs


def _declared_dependency_modules(project_root: str) -> frozenset[str]:
    """Lowercased top-level import names declared as dependencies of this project.

    Sources: pyproject ``[project] dependencies`` + every optional-dependency
    group, plus any ``requirements*.txt`` at the root. A declared dependency
    cannot be a hallucinated import — it just is not in the index (we index
    the repo, not site-packages). Dogfooded on this repo: without this
    allowlist the firewall flagged ``click`` (a literal [project] dependency)
    as unresolved in every file that imports it.
    """
    reqs = _pyproject_requirements(project_root) + _requirements_txt_requirements(project_root)
    return frozenset(name.lower() for req in reqs if (name := _import_name_for_requirement(req)))


# Node.js built-in modules — the JS analog of _PYTHON_STDLIB. 42 entries
# (the stable core set; both bare and `node:`-prefixed forms are accepted).
# Dogfooded on a Node/TS server repo: without this, `import crypto from
# "crypto"` FAILed as a hallucination in every server file.
_NODE_BUILTINS: frozenset[str] = frozenset(
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


def _is_node_builtin(module_path: str) -> bool:
    """True for Node built-ins, bare (``crypto``) or prefixed (``node:crypto``),
    including subpaths (``fs/promises``)."""
    if module_path.startswith("node:"):
        return True
    name = module_path
    return name.split("/")[0] in _NODE_BUILTINS


_JS_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")

# Hard cap on workspace package.json files read per scan — keeps a pathological
# `workspaces: ["**"]` monorepo bounded.
_MAX_WORKSPACE_PACKAGE_FILES = 50


def _iter_pattern_package_files(project_root: str, pattern: str):
    """Yield existing ``package.json`` paths under one workspace glob *pattern*."""
    import glob as _glob

    for hit in sorted(_glob.glob(os.path.join(project_root, pattern))):
        pkg = os.path.join(hit, "package.json")
        if os.path.isfile(pkg):
            yield pkg


def _workspace_package_files(project_root: str, workspaces) -> list[str]:
    """Glob workspace patterns (npm/yarn/pnpm-style ``packages/*``) relative to
    *project_root* and return the existing workspace ``package.json`` paths,
    capped at :data:`_MAX_WORKSPACE_PACKAGE_FILES`. Best-effort: a malformed
    ``workspaces`` value yields an empty list."""
    if isinstance(workspaces, dict):  # yarn object form: {"packages": [...]}
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list):
        return []
    patterns = [p for p in workspaces if isinstance(p, str) and p]
    files: list[str] = []
    for pattern in patterns:
        for pkg in _iter_pattern_package_files(project_root, pattern):
            files.append(pkg)
            if len(files) >= _MAX_WORKSPACE_PACKAGE_FILES:
                return files
    return files


# Per-process cache keyed on project_root: the helper used to be re-invoked per
# FILE inside _scan_file_imports (one package.json parse per scanned JS file).
@functools.lru_cache(maxsize=8)
def _declared_js_dependency_packages(project_root: str) -> frozenset[str]:
    """Package names declared in package.json (dependencies, devDependencies,
    peerDependencies, optionalDependencies). The npm analog of
    :func:`_declared_dependency_modules`: a declared package cannot be a
    hallucinated import — node_modules is never indexed. Dogfooded on a Vue3
    app: without this, `import { ref } from "vue"` flagged `vue` in every
    SFC.

    Monorepo-aware: when the root package.json declares ``workspaces`` (array
    or yarn ``{"packages": [...]}`` object), the dep sections of each globbed
    workspace package.json are merged in, PLUS each workspace package's own
    ``name`` — intra-monorepo imports like ``@myorg/utils`` must resolve even
    though only the consuming app declares them transitively."""
    pkg = os.path.join(project_root, "package.json")
    if not os.path.isfile(pkg):
        return frozenset()
    data = _read_package_json(pkg, "verify_imports.declared_deps.package_json")
    if data is None:
        return frozenset()
    names = _dep_section_names(data)
    for ws_pkg in _workspace_package_files(project_root, data.get("workspaces")):
        ws_data = _read_package_json(ws_pkg, "verify_imports.declared_deps.workspace_package_json")
        if ws_data is None:
            continue
        names |= _dep_section_names(ws_data)
        ws_name = ws_data.get("name")
        if isinstance(ws_name, str) and ws_name:
            names.add(ws_name)
    return frozenset(names)


def _read_package_json(path: str, swallow_key: str) -> dict | None:
    """Parse one package.json; a missing/broken file logs under *swallow_key*
    and returns None (best-effort contract shared by root + workspace reads)."""
    import json as _json

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return _json.load(fh)
    except (OSError, ValueError) as exc:
        from roam.observability import log_swallowed

        log_swallowed(swallow_key, exc)
        return None


def _dep_section_names(data: dict) -> set[str]:
    """Package names across the four dependency sections of a package.json."""
    names: set[str] = set()
    for key in _JS_DEP_SECTIONS:
        names.update((data.get(key) or {}).keys())
    return names


@functools.lru_cache(maxsize=128)
def _dependency_packages_from_package_json(package_json: str) -> frozenset[str]:
    """Read one package.json dependency set, cached across importing files."""
    data = _read_package_json(package_json, "verify_imports.declared_deps.package_json")
    return frozenset(_dep_section_names(data)) if data is not None else frozenset()


def _nearest_js_dependency_packages(project_root: str, file_path: str) -> frozenset[str]:
    """Return dependencies from the nearest package.json above *file_path*."""
    root = os.path.abspath(project_root)
    directory = os.path.dirname(os.path.abspath(os.path.join(root, file_path)))
    try:
        if os.path.commonpath((root, directory)) != root:
            return frozenset()
    except ValueError:
        return frozenset()

    while True:
        package_json = os.path.join(directory, "package.json")
        if os.path.isfile(package_json):
            return _dependency_packages_from_package_json(package_json)
        if directory == root:
            return frozenset()
        parent = os.path.dirname(directory)
        if parent == directory:
            return frozenset()
        directory = parent


def _consume_string_char(text: str, i: int, out: list[str]) -> tuple[int, bool]:
    """Copy one in-string-literal character (escape pairs stay intact);
    return ``(next_index, still_inside_string)``."""
    c = text[i]
    out.append(c)
    if c == "\\" and i + 1 < len(text):
        out.append(text[i + 1])
        return i + 2, True
    return i + 1, c != '"'


def _comment_end(text: str, i: int) -> int | None:
    """When a ``//`` or ``/* */`` comment starts at *i*, return the index just
    past it (unterminated comments run to EOF); otherwise None."""
    n = len(text)
    if text[i] != "/" or i + 1 >= n:
        return None
    if text[i + 1] == "/":
        j = text.find("\n", i)
        return n if j < 0 else j
    if text[i + 1] == "*":
        j = text.find("*/", i + 2)
        return n if j < 0 else j + 2
    return None


def _strip_jsonc(text: str) -> str:
    """Strip JSONC-isms (``//`` and ``/* */`` comments, trailing commas) so
    ``json.loads`` can parse tsconfig/jsconfig files. String-aware character
    scan — comments inside string literals (``"https://x"``) survive."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        if in_str:
            i, in_str = _consume_string_char(text, i, out)
            continue
        comment_end = _comment_end(text, i)
        if comment_end is not None:
            i = comment_end
            continue
        c = text[i]
        if c == '"':
            in_str = True
        out.append(c)
        i += 1
    # Trailing commas before a closing brace/bracket.
    return re.sub(r",\s*([}\]])", r"\1", "".join(out))


# Per-process cache keyed on project_root (same rationale as
# _declared_js_dependency_packages: computed once per scan, not once per file).
@functools.lru_cache(maxsize=8)
def _js_path_aliases(project_root: str) -> dict[str, list[str]]:
    """``compilerOptions.paths`` aliases from tsconfig.json, else jsconfig.json
    (first file found wins). Returns ``{"@/*": ["./src/*"], ...}``. Best-effort:
    JSONC comments and trailing commas are stripped before parsing; a broken
    config logs and yields ``{}``. Treat the returned dict as read-only — it is
    a shared lru_cache entry."""
    for fname in ("tsconfig.json", "jsconfig.json"):
        cfg = os.path.join(project_root, fname)
        if os.path.isfile(cfg):
            # First file found wins — a broken tsconfig yields {} rather
            # than falling through to jsconfig (matching tsc's behavior).
            return _paths_from_config(cfg)
    return {}


def _paths_from_config(cfg: str) -> dict[str, list[str]]:
    """``compilerOptions.paths`` from one tsconfig/jsconfig file; a broken
    config logs and yields ``{}``."""
    import json as _json

    try:
        with open(cfg, encoding="utf-8", errors="replace") as fh:
            data = _json.loads(_strip_jsonc(fh.read()))
        paths = (data.get("compilerOptions") or {}).get("paths") or {}
        return {
            str(key): [t for t in targets if isinstance(t, str)]
            for key, targets in paths.items()
            if isinstance(targets, list)
        }
    except (OSError, ValueError, AttributeError) as exc:
        from roam.observability import log_swallowed

        log_swallowed("verify_imports.js_path_aliases", exc)
        return {}


def _rewrite_js_alias(specifier: str, aliases: dict[str, list[str]]) -> list[str]:
    """Rewrite *specifier* through tsconfig-style path aliases.

    ``@/components/Modal.vue`` + ``{"@/*": ["./src/*"]}`` ->
    ``["./src/components/Modal.vue"]``. Exact (non-``*``) keys must match the
    whole specifier. Returns every mapped target; empty when no alias key
    matches."""
    out: list[str] = []
    for key, targets in aliases.items():
        if key.endswith("*"):
            prefix = key[:-1]
            if not specifier.startswith(prefix):
                continue
            rest = specifier[len(prefix) :]
            out.extend((t[:-1] + rest) if t.endswith("*") else t for t in targets)
        elif specifier == key:
            out.extend(targets)
    return out


def _js_module_is_declared(module_path: str, js_deps: frozenset[str]) -> bool:
    """True when a bare JS module specifier belongs to a declared package.

    Handles scoped packages (``@scope/pkg`` and ``@scope/pkg/sub``) and
    deep imports (``lodash/debounce`` -> ``lodash``). Relative/absolute
    specifiers are never matched here (they resolve via the file index)."""
    if not js_deps or module_path.startswith((".", "/")):
        return False
    if module_path in js_deps:
        return True
    parts = module_path.split("/")
    top = "/".join(parts[:2]) if module_path.startswith("@") and len(parts) >= 2 else parts[0]
    return top in js_deps


def _is_python_file(language: str | None, file_path: str) -> bool:
    """Return True if the file is a Python source file."""
    if language and language.lower() in ("python", "py"):
        return True
    return file_path.endswith(".py") or file_path.endswith(".pyi")


_TRIPLE_QUOTE_RE = re.compile(r'"""|\'\'\'')


def _track_triple_quote_state(line: str, in_string: str | None) -> tuple[str | None, bool]:
    """Track whether subsequent lines sit inside a Python triple-quoted
    string; *in_string* is the active delimiter or None. Returns
    ``(state_after_line, line_started_inside_string)``.

    Import-shaped text inside docstrings and fixture strings is documentation,
    not imports — without this the scanner flagged its own docstring (a line
    reading "import statements inside ...") and planted-fixture blobs in test
    files. Best-effort parity scan, comment-aware when outside a string; the
    same precision class as the rest of the raw-line scanner."""
    started_inside = in_string is not None
    pos = 0
    while True:
        if in_string is None:
            opener = _next_string_opener(line, pos)
            if opener is None:
                break
            in_string, pos = opener
        else:
            j = line.find(in_string, pos)
            if j < 0:
                break
            pos = j + 3
            in_string = None
    return in_string, started_inside


def _next_string_opener(line: str, pos: int) -> tuple[str, int] | None:
    """Next triple-quote opener in *line* at/after *pos* — unless a ``#``
    comment claims the rest of the line first. Returns ``(delimiter,
    scan_resume_index)`` or None."""
    m = _TRIPLE_QUOTE_RE.search(line, pos)
    hash_idx = line.find("#", pos)
    if m is None or (0 <= hash_idx < m.start()):
        return None
    return m.group(0), m.end()


# ---------------------------------------------------------------------------
# Import pattern regexes
# ---------------------------------------------------------------------------

# Python: import X, from X import Y
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)")
_PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+([\w*][\w,\s*]*)")

# JavaScript / TypeScript: import { X } from 'Y', import X from 'Y', require('X')
_JS_IMPORT_FROM = re.compile(r"""^\s*import\s+(?:\{([^}]+)\}\s+from|(\w+)\s+from)\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""")

# Go: import "pkg" or import ( "pkg" )
_GO_IMPORT = re.compile(r"""^\s*(?:import\s+)?["']([^"']+)["']""")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _extract_import_names_from_line(line: str, language: str | None) -> list[str]:
    """Extract imported symbol/module names from a single source line.

    Returns a list of name strings that should be validated against the index.

    Language is checked first so JS-style imports (``import Bar from 'x'``)
    in ``.js``/``.ts``/``.vue``/``.svelte`` files don't accidentally hit
    the Python ``import Bar`` regex — that misattribution made every Vue
    SFC import look like a 1-name Python import and dropped the module
    path entirely.
    """
    names: list[str] = []
    lang = (language or "").lower()
    is_js_like = lang in (
        "javascript",
        "typescript",
        "tsx",
        "jsx",
        "vue",
        "svelte",
    )

    # JavaScript / TypeScript / Vue SFC / Svelte: try JS shapes first.
    if is_js_like:
        m = _JS_IMPORT_FROM.match(line)
        if m:
            # Module-path-only contract (2026-06-12, mirrors the Python
            # from-import fix): braced/default names are MEMBERS of the
            # module and cannot be validated against the index — `import
            # { ref } from "vue"` flagged `ref` and `vue` on every Vue file
            # (30 FPs on one SFC). The FULL module path is kept (not the
            # last segment) so package.json matching sees scoped packages.
            names.append(m.group(3))
            return names

        m = _JS_REQUIRE.search(line)
        if m:
            names.append(m.group(1))
            return names
        # JS-like files don't fall through to the Python regex.
        return names

    # Python
    m = _PY_FROM_IMPORT.match(line)
    if m:
        # Validate the MODULE path only. Member names cannot be reliably
        # validated against the index: stdlib members (``from collections
        # import defaultdict``) are not modules, and internal re-exports /
        # ``__init__`` aliases are not always indexed symbols — dogfooded
        # 2026-06-12: member checking produced 28 false positives across 4
        # of this repo's own files while the module check alone caught the
        # planted hallucination. Precision-first: the module IS the
        # hallucination signal.
        names.append(m.group(1))
        return names

    m = _PY_IMPORT.match(line)
    if m:
        names.append(m.group(1))
        return names

    # JavaScript / TypeScript (fallback for files of unknown language)
    m = _JS_IMPORT_FROM.match(line)
    if m:
        braced = m.group(1)
        default = m.group(2)
        module_path = m.group(3)
        names.append(module_path.split("/")[-1])  # last segment
        if braced:
            for part in braced.split(","):
                part = part.strip()
                if part:
                    name = part.split(" as ")[0].strip() if " as " in part else part
                    names.append(name)
        if default:
            names.append(default)
        return names

    m = _JS_REQUIRE.search(line)
    if m:
        module_path = m.group(1)
        names.append(module_path.split("/")[-1])
        return names

    # Go
    if lang in ("go",):
        m = _GO_IMPORT.match(line)
        if m:
            pkg = m.group(1)
            names.append(pkg.split("/")[-1])
            return names

    return names


def _get_file_language(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    lang_by_path: dict[str, str | None] | None = None,
) -> str | None:
    """Look up language for a file path from the index.

    When ``lang_by_path`` (a ``{path: language}`` map built once per run) is
    supplied, the lookup is an O(1) dict hit instead of a per-call SELECT —
    same result, including ``None`` for an unknown path. Callers without the
    map fall back to the single-row query.
    """
    if lang_by_path is not None:
        return lang_by_path.get(file_path)
    row = conn.execute("SELECT language FROM files WHERE path = ?", (file_path,)).fetchone()
    return row["language"] if row else None


class _FilePathIndex:
    """In-memory index over ``files.path`` mirroring the ``path LIKE`` fallbacks.

    Built ONCE per run from the same ``files`` rows the per-miss ``path LIKE``
    queries scanned. Every miss in :func:`_check_name_exists` otherwise fired a
    LEADING-wildcard ``LIKE`` (``%/{name}.%`` etc.) — un-indexable, so a full
    scan of ``files`` per unresolved import (~9.6k scans on roam-code). These
    structures make each check an O(1) set hit (O(bucket) for the rare
    underscore case).

    SQLite ``LIKE`` semantics replicated exactly:
      * ASCII-case-insensitive — keys are lowercased; the matcher lowercases
        the probe name.
      * ``_`` in the probe name is a single-char wildcard — handled via
        length-bucketed char-by-char compare (``_`` matches any one char).
      * ``%`` cannot appear in an import name (the extractor regexes restrict
        names to ``[\\w.]`` / path last-segments), and ``/`` likewise — names
        carrying either route to the caller's SQL fallback so correctness is
        never traded for the fast path.
    """

    def __init__(self, paths: list[str]) -> None:
        # path LIKE '%/{name}.%' : a '/'-preceded segment whose text up to its
        #   first '.' equals {name} (as a LIKE pattern).
        # path LIKE '{name}.%'   : the whole path's text up to its first '.'
        #   equals {name} (root-relative; '%' prefix absent).
        # Both reduce to: {name} == <stem before first '.'> of some segment,
        # so one stem set serves the OR of the two patterns.
        seg_stems: set[str] = set()
        # path LIKE '%/{name}' : basename (segment after last '/') equals {name}.
        basenames: set[str] = set()
        # path = {name} : exact, case-SENSITIVE equality (no LIKE, no wildcard).
        exact: set[str] = set()
        # Package modules from ``pkg/__init__.py``. The file-stem probes catch
        # ``pkg.mod`` via the final ``mod`` segment, but package imports such as
        # ``from roam.runtime import hotspots`` validate the package itself
        # (``roam.runtime``). Treat every suffix of the package path as
        # importable so src-layout paths like ``src/roam/runtime/__init__.py``
        # resolve as both ``roam.runtime`` and ``runtime``.
        package_modules: set[str] = set()
        for p in paths:
            exact.add(p)
            pl = p.lower()
            sl = pl.rfind("/")
            basenames.add(pl[sl + 1 :] if sl >= 0 else pl)
            if pl.endswith("/__init__.py") or pl == "__init__.py":
                parts = pl[: -len("/__init__.py")].split("/") if pl != "__init__.py" else []
                for start in range(len(parts)):
                    package_modules.add(".".join(parts[start:]))
            # whole-path stem (pattern '{name}.%')
            dot = pl.find(".")
            if dot > 0:
                seg_stems.add(pl[:dot])
            # per-'/'-segment stems (pattern '%/{name}.%')
            start = 0
            while True:
                j = pl.find("/", start)
                if j < 0:
                    break
                seg = pl[j + 1 :]
                k = seg.find(".")
                if k > 0:
                    seg_stems.add(seg[:k])
                start = j + 1
        self._seg_stems = seg_stems
        self._basenames = basenames
        self._exact = exact
        self._seg_stems_by_len: dict[int, list[str]] = defaultdict(list)
        for s in seg_stems:
            self._seg_stems_by_len[len(s)].append(s)
        self._basenames_by_len: dict[int, list[str]] = defaultdict(list)
        for b in basenames:
            self._basenames_by_len[len(b)].append(b)
        self._package_modules = package_modules

    @staticmethod
    def _like_set_match(name_lower: str, exact_set: set[str], by_len: dict[int, list[str]]) -> bool:
        """True iff *name_lower* matches an entry treating ``_`` as a wildcard."""
        if "_" not in name_lower:
            return name_lower in exact_set
        bucket = by_len.get(len(name_lower))
        if not bucket:
            return False
        for cand in bucket:
            if all(a == "_" or a == c for a, c in zip(name_lower, cand)):
                return True
        return False

    def module_file_match(self, name: str) -> bool:
        """Mirror ``path LIKE '%/{name}.%' OR path LIKE '{name}.%'``."""
        return self._like_set_match(name.lower(), self._seg_stems, self._seg_stems_by_len)

    def sfc_file_match(self, name: str) -> bool:
        """Mirror ``path LIKE '%/{name}' OR path = {name}`` (Vue/Svelte SFC)."""
        if self._like_set_match(name.lower(), self._basenames, self._basenames_by_len):
            return True
        # path = ? is exact case-sensitive equality.
        return name in self._exact

    def package_module_match(self, name: str) -> bool:
        """Return True when *name* maps to a package ``__init__.py`` path."""
        return name.lower() in self._package_modules


def _build_file_path_index(conn: sqlite3.Connection) -> _FilePathIndex:
    """Load every ``files.path`` once into a :class:`_FilePathIndex`."""
    paths = [r["path"] for r in conn.execute("SELECT path FROM files")]
    return _FilePathIndex(paths)


def _check_name_exists(
    conn: sqlite3.Connection,
    name: str,
    *,
    symbol_names: set[str] | None = None,
    symbol_qnames: set[str] | None = None,
    file_index: _FilePathIndex | None = None,
) -> bool:
    """Check if a name exists as a symbol name, qualified_name, or file path.

    When both pre-loaded ``symbol_names`` / ``symbol_qnames`` sets are supplied
    (built once per run), the dominant ``name = ? OR qualified_name = ?`` probe
    — which runs for every import name — becomes an O(1) set membership instead
    of a per-name SELECT. This is exact: membership in either set is true iff
    the query would return a row.

    The file-path fallbacks fire on a symbol-table miss. When ``file_index``
    (a :class:`_FilePathIndex` built once per run) is supplied, those
    leading-wildcard ``path LIKE`` queries — a full scan of ``files`` per miss —
    become in-memory set lookups with semantics identical to the SQL. Callers
    without the index fall back to the direct queries. The dotted-module
    fallback stays a symbol-table query (cheap, indexable).
    """
    # Check symbols table (set fast-path when both preloaded; else query)
    if symbol_names is not None and symbol_qnames is not None:
        if name in symbol_names or name in symbol_qnames:
            return True
    else:
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? OR qualified_name = ? LIMIT 1",
            (name, name),
        ).fetchone()
        if row:
            return True

    if file_index is not None:
        if file_index.package_module_match(name):
            return True
    else:
        module_path = name.replace(".", "/")
        row = conn.execute(
            "SELECT 1 FROM files WHERE path LIKE ? OR path = ? LIMIT 1",
            (f"%/{module_path}/__init__.py", f"{module_path}/__init__.py"),
        ).fetchone()
        if row:
            return True

    # Vue / Svelte SFC import: name retains the extension
    # (e.g. ``import Bar from '@/components/Bar.vue'`` extracts ``Bar.vue``).
    # Match the file by exact filename (``%/Bar.vue`` or ``Bar.vue``) and,
    # for the symbol form, by stem (``Bar`` is synthesised as a component
    # symbol by the TypeScript extractor for every .vue / .svelte file).
    lower = name.lower()
    if lower.endswith(".vue") or lower.endswith(".svelte"):
        # A '/' or '%' in the name escapes the in-memory index's segment model
        # (import names never carry either, so this fallback is effectively
        # dead — but it keeps the fast path provably exact).
        if file_index is not None and "/" not in name and "%" not in name:
            sfc_hit = file_index.sfc_file_match(name)
        else:
            sfc_hit = (
                conn.execute(
                    "SELECT 1 FROM files WHERE path LIKE ? OR path = ? LIMIT 1",
                    (f"%/{name}", name),
                ).fetchone()
                is not None
            )
        if sfc_hit:
            return True
        # Fallback: synthesised component symbol uses the stem
        stem = name.rsplit(".", 1)[0]
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1",
            (stem,),
        ).fetchone()
        if row:
            return True

    # Check if it matches a file path (module name -> file)
    # e.g. "models" -> "models.py" or "src/models.py"
    if file_index is not None and "/" not in name and "%" not in name:
        if file_index.module_file_match(name):
            return True
    else:
        row = conn.execute(
            "SELECT 1 FROM files WHERE path LIKE ? OR path LIKE ? LIMIT 1",
            (f"%/{name}.%", f"{name}.%"),
        ).fetchone()
        if row:
            return True

    # Check for dotted module path (e.g. "os.path" -> look for "path" in symbols)
    if "." in name:
        last_part = name.rsplit(".", 1)[-1]
        row = conn.execute(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1",
            (last_part,),
        ).fetchone()
        if row:
            return True
        # The last segment of an internal dotted module path is a FILE, not
        # a symbol ("roam.capability" -> src/roam/capability.py defines no
        # symbol named "capability"). Dogfooded on this repo: the symbols
        # probe alone flagged the package's own modules as unresolved. Try
        # the same module-file match the bare-name path uses.
        if file_index is not None and "/" not in last_part and "%" not in last_part:
            if file_index.module_file_match(last_part):
                return True
        else:
            row = conn.execute(
                "SELECT 1 FROM files WHERE path LIKE ? OR path LIKE ? LIMIT 1",
                (f"%/{last_part}.%", f"{last_part}.%"),
            ).fetchone()
            if row:
                return True

    return False


def _fts_suggestions(conn: sqlite3.Connection, name: str, limit: int = 3) -> list[str]:
    """Use FTS5 to find fuzzy matches for an unresolved import name."""
    suggestions: list[str] = []

    # Tokenize the query
    tokens = name.replace("_", " ").replace(".", " ").split()
    if not tokens:
        return suggestions

    try:
        fts_query = " OR ".join(f'"{t}"*' for t in tokens)
        rows = conn.execute(
            "SELECT s.name, s.qualified_name, f.path as file_path "
            "FROM symbol_fts sf "
            "JOIN symbols s ON sf.rowid = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE symbol_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        for r in rows:
            display = r["qualified_name"] or r["name"]
            if display not in suggestions:
                suggestions.append(display)
    except sqlite3.Error as _fts_exc:
        # FTS5 not available, try LIKE fallback
        from roam.observability import log_swallowed

        log_swallowed("cmd_verify_imports:fts_suggestions", _fts_exc)
        try:
            rows = conn.execute(
                "SELECT s.name, s.qualified_name "
                "FROM symbols s "
                "WHERE s.name LIKE ? COLLATE NOCASE "
                "ORDER BY s.name LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
            for r in rows:
                display = r["qualified_name"] or r["name"]
                if display not in suggestions:
                    suggestions.append(display)
        except sqlite3.Error as _like_exc:
            log_swallowed("cmd_verify_imports:like_suggestions_fallback", _like_exc)

    return suggestions


def _get_edge_imports(conn: sqlite3.Connection, file_path: str | None) -> list[dict]:
    """Get import edges from the edges table, optionally filtered by file.

    W543-followup: source the IN-clause from
    :func:`roam.db.edge_kinds.import_in_clause` so the verifier matches
    plugin-emitted ``'imports'`` rows alongside the canonical singular
    ``'import'``.
    """
    kind_clause = import_in_clause("e.kind")
    if file_path:
        rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.line, "
            "s_src.name AS source_name, s_src.qualified_name AS source_qname, "
            "s_tgt.name AS target_name, s_tgt.qualified_name AS target_qname, "
            "f.path AS file_path "
            "FROM edges e "
            "JOIN symbols s_src ON e.source_id = s_src.id "
            "LEFT JOIN symbols s_tgt ON e.target_id = s_tgt.id "
            "JOIN files f ON s_src.file_id = f.id "
            f"WHERE {kind_clause} AND f.path = ?",
            (file_path,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.line, "
            "s_src.name AS source_name, s_src.qualified_name AS source_qname, "
            "s_tgt.name AS target_name, s_tgt.qualified_name AS target_qname, "
            "f.path AS file_path "
            "FROM edges e "
            "JOIN symbols s_src ON e.source_id = s_src.id "
            "LEFT JOIN symbols s_tgt ON e.target_id = s_tgt.id "
            "JOIN files f ON s_src.file_id = f.id "
            f"WHERE {kind_clause}"
        ).fetchall()

    return [dict(r) for r in rows]


def _import_scan_entry(file_path: str, line_num: int, name: str, *, resolved: bool) -> dict:
    """Build the per-import scan row used by text/JSON/SARIF consumers."""
    return {
        "file": file_path,
        "line": line_num,
        "name": name,
        "status": "resolved" if resolved else "unresolved",
        "suggestions": [],
    }


# 7 extensions: the Node/TypeScript module-resolution set tried for extensionless
# relative imports (.js/.ts/.jsx/.tsx/.mjs/.cjs/.json), matching Node + tsc resolution.
_JS_RESOLUTION_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".json")


def _js_relative_import_resolves(project_root: str, file_path: str, specifier: str) -> bool:
    """Resolve a relative JS/TS specifier from the importing file's directory."""
    if not specifier.startswith(("./", "../")):
        return False

    root = os.path.abspath(project_root)
    importer_dir = os.path.dirname(os.path.abspath(os.path.join(root, file_path)))
    base = os.path.normpath(os.path.join(importer_dir, specifier))
    try:
        if os.path.commonpath((root, base)) != root:
            return False
    except ValueError:
        return False

    candidates = [base]
    candidates.extend(base + extension for extension in _JS_RESOLUTION_EXTENSIONS)
    candidates.extend(os.path.join(base, f"index{extension}") for extension in _JS_RESOLUTION_EXTENSIONS)
    return any(os.path.isfile(candidate) for candidate in candidates)


def _js_directory_import_resolves(conn: sqlite3.Connection, probe: str) -> bool:
    """True when a JS path specifier resolves to an indexed directory."""
    return (
        conn.execute(
            "SELECT 1 FROM files WHERE path LIKE ? LIMIT 1",
            (f"%/{probe}/%",),
        ).fetchone()
        is not None
    )


def _js_alias_import_resolves(
    conn: sqlite3.Connection,
    name: str,
    aliases: dict[str, list[str]],
    *,
    symbol_names: set[str] | None,
    symbol_qnames: set[str] | None,
    file_index: _FilePathIndex | None,
) -> bool:
    """Resolve a JS/TS import through tsconfig/jsconfig path aliases."""
    for target in _rewrite_js_alias(name, aliases):
        probe = target.split("/")[-1].rsplit(".", 1)[0] or target
        if _check_name_exists(
            conn,
            probe,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            file_index=file_index,
        ):
            return True
        if _js_directory_import_resolves(conn, probe):
            return True
    return False


def _probe_name_for_import(name: str, js_deps: frozenset[str] | None) -> str:
    """Return the symbol/file probe name for one extracted import string."""
    if js_deps is not None and "/" in name:
        return name.split("/")[-1].rsplit(".", 1)[0] or name
    return name


def _scan_import_entry(
    conn: sqlite3.Connection,
    file_path: str,
    line_num: int,
    name: str,
    *,
    project_root: str,
    is_py: bool,
    js_deps: frozenset[str] | None,
    js_aliases: dict[str, list[str]],
    symbol_names: set[str] | None,
    symbol_qnames: set[str] | None,
    file_index: _FilePathIndex | None,
    declared_deps: frozenset[str] | None,
) -> dict:
    """Validate one extracted import name and return its scan row."""
    if is_py and _is_stdlib_module(name):
        return _import_scan_entry(file_path, line_num, name, resolved=True)

    if js_deps is not None and name.startswith(("./", "../")):
        resolved = _js_relative_import_resolves(project_root, file_path, name)
        entry = _import_scan_entry(file_path, line_num, name, resolved=resolved)
        if not resolved:
            entry["suggestions"] = _fts_suggestions(conn, name)
        return entry

    if js_deps is not None and (_is_node_builtin(name) or _js_module_is_declared(name, js_deps)):
        return _import_scan_entry(file_path, line_num, name, resolved=True)

    if js_aliases and _js_alias_import_resolves(
        conn,
        name,
        js_aliases,
        symbol_names=symbol_names,
        symbol_qnames=symbol_qnames,
        file_index=file_index,
    ):
        return _import_scan_entry(file_path, line_num, name, resolved=True)

    if js_deps is not None and not name.startswith("/"):
        entry = _import_scan_entry(file_path, line_num, name, resolved=False)
        entry["suggestions"] = _fts_suggestions(conn, name)
        return entry

    probe_name = _probe_name_for_import(name, js_deps)
    if declared_deps and is_py and name.split(".")[0].lower() in declared_deps:
        return _import_scan_entry(file_path, line_num, name, resolved=True)

    resolved = _check_name_exists(
        conn,
        probe_name,
        symbol_names=symbol_names,
        symbol_qnames=symbol_qnames,
        file_index=file_index,
    )
    if not resolved and js_deps is not None and "/" in name:
        resolved = _js_directory_import_resolves(conn, probe_name)

    entry = _import_scan_entry(file_path, line_num, name, resolved=resolved)
    if not resolved:
        entry["suggestions"] = _fts_suggestions(conn, name)
    return entry


def _scan_file_imports(
    conn: sqlite3.Connection,
    file_path: str,
    project_root: str,
    *,
    symbol_names: set[str] | None = None,
    symbol_qnames: set[str] | None = None,
    lang_by_path: dict[str, str | None] | None = None,
    file_index: _FilePathIndex | None = None,
    declared_deps: frozenset[str] | None = None,
) -> list[dict]:
    """Scan a source file for import statements and validate each one.

    Returns a list of import dicts with keys:
        file, line, name, status (resolved/unresolved), suggestions
    """
    full_path = os.path.join(project_root, file_path)
    if not os.path.isfile(full_path):
        return []

    language = _get_file_language(conn, file_path, lang_by_path=lang_by_path)
    lang_lower = (language or "").lower()
    is_js_like = lang_lower in ("javascript", "typescript", "tsx", "jsx", "vue", "svelte")
    # Manifest contents and path aliases are cached across scanned JS files.
    js_deps = _nearest_js_dependency_packages(project_root, file_path) if is_js_like else None
    js_aliases = _js_path_aliases(project_root) if is_js_like else {}
    results: list[dict] = []
    seen: set[tuple[str, int]] = set()

    is_py = _is_python_file(language, file_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            prev_stripped = ""
            in_triple: str | None = None
            optional_import_indent: int | None = None
            for line_num, line in enumerate(f, start=1):
                stripped = line.strip()
                indent = len(line) - len(line.lstrip(" "))
                if is_py:
                    # Lines inside triple-quoted strings are string content
                    # (docstrings, test fixtures), not imports. They do not
                    # update prev_stripped — a `try:` inside a docstring must
                    # not arm the optional-import guard below.
                    in_triple, started_inside = _track_triple_quote_state(line, in_triple)
                    if started_inside:
                        continue
                # Comment lines are documentation, not imports — without this
                # the scanner matched import shapes inside its OWN regex-doc
                # comments (dogfooded: `require('X')` in a comment flagged X).
                if is_py and stripped.startswith("#"):
                    prev_stripped = stripped
                    continue
                # A try-guarded import is a deliberate OPTIONAL dependency
                # (`try: import orjson` + clean fallback) — by definition not
                # a hallucination. Skip the import block directly under
                # ``try:`` (not just its first line), so multi-import optional
                # adapters like reportlab/watchdog don't leave a residue row.
                is_import_line = stripped.startswith(("import ", "from "))
                if optional_import_indent is not None and (indent < optional_import_indent or not is_import_line):
                    optional_import_indent = None
                if is_import_line and (prev_stripped == "try:" or optional_import_indent == indent):
                    optional_import_indent = indent
                    prev_stripped = stripped
                    continue
                if stripped:
                    prev_stripped = stripped
                import_names = _extract_import_names_from_line(line, language)
                for name in import_names:
                    key = (name, line_num)
                    if key in seen:
                        continue
                    seen.add(key)

                    results.append(
                        _scan_import_entry(
                            conn,
                            file_path,
                            line_num,
                            name,
                            project_root=project_root,
                            is_py=is_py,
                            js_deps=js_deps,
                            js_aliases=js_aliases,
                            symbol_names=symbol_names,
                            symbol_qnames=symbol_qnames,
                            file_index=file_index,
                            declared_deps=declared_deps,
                        )
                    )
    except (OSError, UnicodeDecodeError) as _exc:
        from roam.observability import log_swallowed

        log_swallowed("cmd_verify_imports:source_scan", _exc)

    return results


def verify_imports_for_connection(
    conn: sqlite3.Connection,
    project_root: str,
    file_filter: str | None = None,
) -> dict:
    """Run the full import verification pipeline against an open DB connection.

    Parameters
    ----------
    conn:
        Open DB connection (readonly is fine for reads; we don't write).
    project_root:
        Absolute path to the project root directory.
    file_filter:
        Optional file path to restrict scanning to a single file.

    Returns
    -------
    dict with keys: imports (list), total, resolved, unresolved, files_checked
    """
    # 1. Determine which files to check
    if file_filter:
        # Normalize the filter path
        norm = file_filter.replace("\\", "/")
        row = conn.execute(
            "SELECT path FROM files WHERE path = ? OR path LIKE ?",
            (norm, f"%{norm}"),
        ).fetchone()
        if row:
            file_paths = [row["path"]]
        else:
            file_paths = [norm]
    else:
        rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        file_paths = [r["path"] for r in rows]

    # Pre-load symbol names / qualified names + a file->language map ONCE so the
    # per-import resolution probe and per-file language lookup are in-memory
    # set/dict hits instead of N+1 SELECTs (the file-scan phase otherwise fired
    # up to ~5 queries per import name -> tens of thousands of round-trips).
    # Output-preserving: set membership is exact for the symbols name/qname
    # probe, and dict.get matches the single-row language SELECT (None for an
    # unknown path).
    symbol_names: set[str] = set()
    symbol_qnames: set[str] = set()
    for r in conn.execute("SELECT name, qualified_name FROM symbols"):
        if r["name"]:
            symbol_names.add(r["name"])
        if r["qualified_name"]:
            symbol_qnames.add(r["qualified_name"])
    lang_by_path: dict[str, str | None] = {
        r["path"]: r["language"] for r in conn.execute("SELECT path, language FROM files")
    }
    # Pre-load the file-path index ONCE so the per-miss ``path LIKE`` fallbacks
    # in ``_check_name_exists`` (leading-wildcard -> un-indexable full scan of
    # ``files`` per unresolved import) become in-memory set lookups. Semantics
    # are identical to the SQL (see _FilePathIndex docstring).
    file_index = _build_file_path_index(conn)
    declared_deps = _declared_dependency_modules(project_root)

    # 2. Scan each file
    all_imports: list[dict] = []
    files_checked: set[str] = set()

    for fp in file_paths:
        file_imports = _scan_file_imports(
            conn,
            fp,
            project_root,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            lang_by_path=lang_by_path,
            file_index=file_index,
            declared_deps=declared_deps,
        )
        if file_imports:
            files_checked.add(fp)
            all_imports.extend(file_imports)

    # 3. Also check edge-based imports from the DB.
    # (file, name) index for O(1) edge-dedup against already-found imports —
    # replaces an O(n) linear scan per edge, kept in sync as new edge imports
    # are appended (matches the original growing-scan semantics exactly).
    seen_file_name: set[tuple[str, str]] = {(i["file"], i["name"]) for i in all_imports}
    edge_imports = _get_edge_imports(conn, file_filter)
    for edge in edge_imports:
        target_name = edge.get("target_name") or ""
        if not target_name:
            continue
        # Check if we already found this import from file scanning
        fp = edge["file_path"]
        line = edge.get("line") or 0
        if (fp, target_name) in seen_file_name:
            continue

        # Skip Python stdlib modules in edge-based imports too
        edge_lang = _get_file_language(conn, fp, lang_by_path=lang_by_path)
        if _is_python_file(edge_lang, fp) and _is_stdlib_module(target_name):
            all_imports.append(
                {
                    "file": fp,
                    "line": line,
                    "name": target_name,
                    "status": "resolved",
                    "suggestions": [],
                }
            )
            files_checked.add(fp)
            seen_file_name.add((fp, target_name))
            continue

        resolved = edge["target_id"] is not None and _check_name_exists(
            conn,
            target_name,
            symbol_names=symbol_names,
            symbol_qnames=symbol_qnames,
            file_index=file_index,
        )
        entry: dict = {
            "file": fp,
            "line": line,
            "name": target_name,
            "status": "resolved" if resolved else "unresolved",
            "suggestions": [],
        }
        if not resolved:
            entry["suggestions"] = _fts_suggestions(conn, target_name)
        all_imports.append(entry)
        files_checked.add(fp)
        seen_file_name.add((fp, target_name))

    total = len(all_imports)
    resolved = sum(1 for i in all_imports if i["status"] == "resolved")
    unresolved = total - resolved

    return {
        "imports": all_imports,
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "files_checked": len(files_checked),
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="verify-imports",
    category="workflow",
    summary="Validate import/require statements against the indexed symbol table",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("verify-imports")
@click.option("--path", "file_path", default=None, help="Restrict verification to a single file path.")
@click.option(
    "--file",
    "file_path",
    default=None,
    hidden=True,
    help="Deprecated alias for --path. Retained for backward compatibility.",
)
@click.pass_context
def verify_imports_cmd(ctx, file_path):
    """Validate import/require statements against the indexed symbol table.

    Flags unresolvable imports and suggests corrections via fuzzy matching.
    Acts as a hallucination firewall for AI-generated code.

    Unlike ``search`` (which finds symbols by name) and ``relate`` (which shows
    symbol relationships), this command validates that import statements in source
    files resolve to indexed symbols -- a hallucination firewall for AI-generated
    imports.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = str(find_project_root())

    with open_db(readonly=True) as conn:
        result = verify_imports_for_connection(conn, project_root, file_filter=file_path)

        # --- SARIF output (W1229) ------------------------------------------
        # SARIF surfaces the closed-enum classification rule catalogue
        # (invalid-import / hallucination-import) even on a clean scan so
        # CI consumers see the rule vocabulary regardless of whether any
        # import fired. ``resolved`` rows are filtered upstream by
        # ``verify_imports_to_sarif`` (not actionable). Language is
        # stamped onto each row inside this branch so the SARIF message
        # body can prefix the imported name with the producer's
        # ``language`` column (the JSON envelope keeps the per-import
        # record compact and elides the language field — only the SARIF
        # branch consumes it).
        if sarif_mode:
            from roam.output.sarif import verify_imports_to_sarif, write_sarif

            sarif_findings: list[dict] = []
            for i in result["imports"]:
                if i.get("status") != "unresolved":
                    continue
                lang = _get_file_language(conn, i["file"]) or ""
                sarif_findings.append(
                    {
                        "file": i["file"],
                        "line": i["line"],
                        "name": i["name"],
                        "status": i["status"],
                        "language": lang,
                        "suggestions": i.get("suggestions", []),
                    }
                )
            click.echo(write_sarif(verify_imports_to_sarif(sarif_findings)))
            return

    imports = result["imports"]
    total = result["total"]
    resolved = result["resolved"]
    unresolved = result["unresolved"]
    files_checked = result["files_checked"]

    # Build verdict
    if total == 0:
        verdict = "No imports found to verify"
    elif unresolved == 0:
        verdict = f"All {total} imports resolved across {files_checked} files"
    else:
        verdict = f"{unresolved} unresolved imports out of {total} in {files_checked} files"

    # --- JSON output ---
    if json_mode:
        # Filter to unresolved for compact JSON; include all if few
        import_records = []
        for i in imports:
            rec: dict = {
                "file": i["file"],
                "line": i["line"],
                "name": i["name"],
                "status": i["status"],
            }
            if i["suggestions"]:
                rec["suggestions"] = i["suggestions"]
            import_records.append(rec)

        envelope = json_envelope(
            "verify-imports",
            summary={
                "verdict": verdict,
                "total_imports": total,
                "resolved": resolved,
                "unresolved": unresolved,
                "files_checked": files_checked,
            },
            budget=token_budget,
            imports=import_records,
        )
        click.echo(to_json(envelope))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if unresolved > 0:
        rows = []
        for i in imports:
            if i["status"] != "unresolved":
                continue
            loc_str = f"{i['file']}:{i['line']}"
            suggestions_str = ", ".join(i["suggestions"]) if i["suggestions"] else "-"
            rows.append([loc_str, i["name"], suggestions_str])

        click.echo(
            format_table(
                ["Location", "Import", "Suggestions"],
                rows,
            )
        )
        click.echo()
        click.echo(f"  {unresolved} unresolved, {resolved} resolved, {files_checked} files checked")
        click.echo()
        click.echo("  Tip: Run `roam search <name>` for more details on a symbol.")
        click.echo("       If recently added, run `roam index` to refresh.")
    else:
        if total > 0:
            click.echo(f"  All {total} imports verified successfully.")
        else:
            click.echo("  No import statements found in indexed files.")
