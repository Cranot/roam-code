"""Trace from a changed symbol or file to test files that exercise it.

A test is left out only when the analysis proves it cannot reach the
change.  Besides the test module, it reads the conftests that apply, the
modules named in ``pytest_plugins`` and the project modules these import,
up to 3 imports deep (``_LOADED_DEPTH``): an environment write or process
start there keeps the test as possible reach, never as proven coverage.  A
conftest fixture a test requests by name, or one marked ``autouse``, is
read as part of the test.  An import under ``if TYPE_CHECKING:`` never runs
and proves nothing.

Blind spots, named rather than chased: dynamic imports (``importlib``,
``__import__`` with a computed name); environment setters and process
launchers fetched through ``getattr`` (``getattr(subprocess, 'run')``);
argv changed at run time before the launch, by ``setup_module`` or other
module-level mutation; fixtures requested dynamically through
``request.getfixturevalue``; plugins registered by entry points or the -p
flag; launches and environment writes in production code the test calls
(the package that owns the command registry); and anything more than 3
imports away from the test module.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.changed_files import (
    get_changed_files,
    is_test_file,
    resolve_changed_to_db,
)
from roam.commands.resolve import ensure_index, find_symbol, resolve_file_symbols
from roam.db.connection import batched_in, find_project_root, open_db
from roam.index.test_conventions import find_test_candidates
from roam.output.formatter import (
    abbrev_kind,
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)

_MAX_HOPS = 10


def _load_frontier_callers_without_node_queries(conn, frontier_ids):
    """Return reverse callers grouped by target for one BFS frontier."""
    rows = batched_in(
        conn,
        "SELECT e.target_id, e.source_id, s.name "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id "
        "WHERE e.target_id IN ({ph})",
        frontier_ids,
    )
    callers_by_target = {}
    for row in rows:
        callers_by_target.setdefault(row["target_id"], []).append(row)
    return callers_by_target


# ---------------------------------------------------------------------------
# BFS reverse-edge walker
# ---------------------------------------------------------------------------


def _bfs_reverse_callers(conn, start_ids, stop_ids=frozenset()):
    """Walk reverse edges (callers) via BFS up to _MAX_HOPS.

    *start_ids* is a set of symbol IDs to begin from.  Symbols in
    *stop_ids* are recorded when reached but never expanded: a keyed
    command registry reaches the dispatcher, and the dispatcher reaches
    every test of every command, so walking through it selects the whole
    CLI suite for any one handler.

    Returns a dict ``{symbol_id: (hop_count, via_name)}`` for every
    reachable caller.  *via_name* is the name of the first symbol on
    the path from the start set that led us here (useful for the
    "via" label in transitive results).
    """
    visited = {}  # symbol_id -> (hops, via_name)
    frontier = []  # (symbol_id, hops, via_name)

    for sid in start_ids:
        visited[sid] = (0, None)
        frontier.append((sid, 0, None))

    while frontier:
        expandable = [(sid, hops, via) for sid, hops, via in frontier if hops < _MAX_HOPS]
        if not expandable:
            break

        callers_by_target = _load_frontier_callers_without_node_queries(
            conn,
            [sid for sid, _hops, _via in expandable],
        )
        next_frontier = []

        for current_id, hops, via in expandable:
            for row in callers_by_target.get(current_id, []):
                caller_id = row["source_id"]
                caller_name = row["name"]
                new_hops = hops + 1
                # The "via" label is the name of the node at hop 1 that started
                # this path (i.e. the direct caller of the target).
                new_via = via if via else caller_name

                if caller_id not in visited or visited[caller_id][0] > new_hops:
                    visited[caller_id] = (new_hops, new_via)
                    if caller_id not in stop_ids:
                        next_frontier.append((caller_id, new_hops, new_via))

        frontier = next_frontier

    return visited


# ---------------------------------------------------------------------------
# Colocated test detection
# ---------------------------------------------------------------------------


def _find_colocated_tests(conn, file_paths):
    """Find test files colocated with the given source files.

    Uses two mechanisms:
    1. Colocated tests in the same directory (e.g., test_*.py / *_test.py)
    2. Convention-based test discovery (e.g., separate test projects for C#)
    """
    # Pre-fetch the entire (path, language) map once. Replaces three
    # nested N+1 queries (per-dir LIKE, per-file language lookup,
    # per-candidate existence check) with a single SELECT and
    # in-memory dict / set lookups.
    path_to_language: dict[str, str | None] = {}
    for r in conn.execute("SELECT path, language FROM files").fetchall():
        path_to_language[r["path"]] = r["language"]
    all_paths = set(path_to_language)
    file_paths_set = set(file_paths)

    # mechanism 1: colocated tests within the same directory subtree.
    # Original code issued one ``WHERE path LIKE 'dir/%'`` per unique
    # input directory — recursive prefix match. Replaced with a single
    # in-memory scan over the pre-fetched all_paths set.
    dirs = set()
    for fp in file_paths:
        d = os.path.dirname(fp.replace("\\", "/"))
        if d:
            dirs.add(d)

    # Pre-classify each path once: which input dirs contain it as a
    # subtree descendant? For each path p, find the dirs in ``dirs``
    # that p starts with (followed by ``/``). Total work is
    # O(len(all_paths) * len(dirs)) but with zero DB round-trips.
    colocated = []
    for p in all_paths:
        if not is_test_file(p) or p in file_paths_set:
            continue
        p_norm = p.replace("\\", "/")
        for d in dirs:
            if p_norm.startswith(d + "/"):
                colocated.append(p)
                break

    # mechanism 2: convention-based test discovery.
    # Use the pre-fetched dict for both the per-file language lookup
    # and the per-candidate existence check — both were N+1 before.
    convention_tests = []
    for fp in file_paths:
        language = path_to_language.get(fp)
        if not language:
            continue
        candidates = find_test_candidates(fp, language=language)
        for candidate in candidates:
            if candidate in all_paths and is_test_file(candidate) and candidate not in file_paths_set:
                convention_tests.append(candidate)

    return sorted(set(colocated + convention_tests))


# ---------------------------------------------------------------------------
# Keyed dispatch registries and test-source relations
# ---------------------------------------------------------------------------


def _python_module_name(path):
    """Dotted module name for a ``.py`` path (``src/`` layout stripped)."""
    p = path.replace("\\", "/")
    if not p.endswith(".py"):
        return None
    mod = p[:-3].replace("/", ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    if mod.startswith("src."):
        mod = mod[len("src.") :]
    return mod or None


def _parse_python(root, path):
    try:
        return ast.parse((root / path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None


def _registry_dict(tree, line):
    """The string-keyed dict literal assigned on *line*, else None."""
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno == line:
            value = node.value
            if (
                isinstance(value, ast.Dict)
                and value.keys
                and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys)
            ):
                return value
    return None


def _keys_for_handler(registry, module, name):
    """Registry keys whose ``("module", "fn")`` value names the handler."""
    exact, by_name = set(), set()
    for key, value in zip(registry.keys, registry.values):
        if not (isinstance(value, ast.Tuple) and len(value.elts) == 2):
            continue
        mod_node, fn_node = value.elts
        if not all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in (mod_node, fn_node)):
            continue
        if fn_node.value == name:
            (exact if mod_node.value == module else by_name).add(key.value)
    return exact or by_name


def _keyed_registries(conn, root):
    """Classify ``dispatch`` edge sources that are string-keyed registries.

    Returns ``(registries, handler_keys)``.  ``registries`` maps each
    registry shaped ``{"name": ("pkg.mod", "fn")}`` to ``(path, name,
    keys)``: its dispatcher selects handlers by key, so reaching the
    registry does not by itself mean a test runs this handler.
    ``handler_keys`` maps ``handler_id -> {key}``.  List-shaped
    registries are iterated whole by their runners and stay on the
    ordinary call-graph walk.
    """
    rows = conn.execute(
        "SELECT e.source_id, e.target_id, s.name AS source_name, s.line_start, f.path, "
        "t.name AS target_name, tf.path AS target_path "
        "FROM edges e "
        "JOIN symbols s ON e.source_id = s.id JOIN files f ON s.file_id = f.id "
        "JOIN symbols t ON e.target_id = t.id JOIN files tf ON t.file_id = tf.id "
        "WHERE e.kind = 'dispatch'"
    ).fetchall()
    trees = {}
    parsed = {}  # registry_id -> ast.Dict | None
    registries = {}
    handler_keys = {}
    for r in rows:
        rid = r["source_id"]
        if rid not in parsed:
            if r["path"] not in trees:
                trees[r["path"]] = _parse_python(root, r["path"])
            parsed[rid] = _registry_dict(trees[r["path"]], r["line_start"])
            if parsed[rid] is not None:
                keys = frozenset(k.value for k in parsed[rid].keys)
                registries[rid] = (r["path"], r["source_name"], keys)
        if parsed[rid] is None:
            continue
        for key in _keys_for_handler(parsed[rid], _python_module_name(r["target_path"]), r["target_name"]):
            handler_keys.setdefault(r["target_id"], set()).add(key)
    return registries, handler_keys


_FROM_IMPORT_RE = re.compile(r"^[ \t]*from[ \t]+(\w[\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n#;]*)", re.M)
_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([^\n#;]+)", re.M)


def _imported_modules(source):
    """Dotted names a Python source imports, including ``from pkg import mod``.

    A line scan rather than ``ast.parse``: it pre-filters the whole test
    tree before any file is parsed.
    """
    modules = set()
    for m in _IMPORT_RE.finditer(source):
        modules.update(part.split()[0] for part in m.group(1).split(",") if part.strip())
    for m in _FROM_IMPORT_RE.finditer(source):
        module = m.group(1)
        modules.add(module)
        for part in m.group(2).strip("()").split(","):
            if part.split():
                modules.add(f"{module}.{part.split()[0]}")
    return modules


def _type_checking(node):
    """``if TYPE_CHECKING:`` / ``if typing.TYPE_CHECKING:``: a branch only a type checker takes."""
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_imports(tree):
    """Dotted names *tree* imports outside ``if TYPE_CHECKING:`` bodies, named as ``_imported_modules`` does."""
    modules, stack = set(), [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If) and _type_checking(node):
            stack.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        stack.extend(ast.iter_child_nodes(node))
    return modules


def _test_module_imports(conn, root, target_modules, exclude):
    """``{test_file: module}`` for Python tests importing a target module."""
    hits = {}
    if not target_modules:
        return hits
    leaves = {m.rsplit(".", 1)[-1] for m in target_modules}
    for row in conn.execute("SELECT path FROM files WHERE language = 'python'").fetchall():
        path = row["path"]
        if path in exclude or not is_test_file(path):
            continue
        try:
            source = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(leaf in source for leaf in leaves):
            continue
        if not target_modules & _imported_modules(source):
            continue
        try:  # an import only a type checker reads never runs: no proof
            imported = _runtime_imports(ast.parse(source))
        except (SyntaxError, ValueError):
            continue
        for module in sorted(target_modules):
            if module in imported:
                hits[path] = module
                break
    return hits


# ---------------------------------------------------------------------------
# Dispatch reach: which registry keys a test runs, may run, or cannot run
# ---------------------------------------------------------------------------
#
# A test is excluded only when the analysis proves it cannot run the
# handler.  A word is a set of values: literal strings plus the markers
# below.  An argv is a tuple of slots ``(values, optional, splice)``; a
# splice stands for any number of words.  A helper's parameters stay
# symbolic (``_Ref``) until a caller binds them, so argv order survives
# the call.


class _Marker:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<{self.name}>"


_UNKNOWN = _Marker("unknown")  # a value the analysis cannot resolve
_EVERY = _Marker("every-key")  # each registry key in turn: a loop over the registry
_PATH = _Marker("path")  # a filesystem path: a word that is never a key
_PYTHON = _Marker("python")  # the running interpreter, ``sys.executable``
_CONDITIONAL = _Marker("conditional")  # a known value whose invocation may be skipped
_EACH = _Marker("each")  # every string value is taken in turn: a loop over literals, parametrize cases


@dataclass(frozen=True)
class _Certain:
    """A value reached in the guaranteed first iteration before an early exit."""

    value: str


@dataclass(frozen=True)
class _Ref:
    """A helper parameter: one word, or (``seq``) a run of argv words."""

    name: str
    seq: bool


_SKIP_PARAMS = frozenset({"self", "cls"})
_MAX_ALTERNATIVES = 32
_MAX_PENDING = 256
# Import hops followed from a test module, its conftests and plugins into the project's own helpers.
_LOADED_DEPTH = 3

_PROCESS_ARGV = frozenset(
    {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen"}
)
_PROCESS_SHELL = frozenset(
    {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput", "asyncio.create_subprocess_shell"}
)
_PROCESS_VARARGS = frozenset({"asyncio.create_subprocess_exec"})
# Process starts whose argv is not read: each keeps possible reach.
_PROCESS_OPAQUE = frozenset(
    {f"os.exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
    | {f"os.spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")}
    | {"os.posix_spawn", "os.posix_spawnp", "os.startfile", "pty.spawn"}
)
_PROCESS_CALLS = _PROCESS_ARGV | _PROCESS_SHELL | _PROCESS_VARARGS | _PROCESS_OPAQUE
# Calls that end a test without failing it or running what follows.
_TERMINATORS = frozenset(
    {"pytest.skip", "pytest.fail", "pytest.xfail", "pytest.exit", "pytest.importorskip"}
    | {"sys.exit", "os._exit", "os.abort", "exit", "quit"}
)
_SELF_TERMINATORS = frozenset({"skipTest", "fail"})  # unittest's ``self.skipTest()`` / ``self.fail()``
_SWALLOWERS = ("raises", "suppress", "assertRaises", "assertRaisesRegex")  # context managers that catch
_PATH_CALLS = frozenset({"str", "os.fspath", "os.path.join", "os.path.abspath", "os.path.realpath"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "powershell", "pwsh"})
_PH_OPEN, _PH_CLOSE = "\ue000", "\ue001"  # an interpolation inside a command string


def _slot(values, optional=False, splice=False):
    return (frozenset(values), optional, splice)


def _concat(prefixes, suffixes):
    """Every prefix followed by every suffix; past the cap, one splice of every word."""
    out = [a + b for a in prefixes for b in suffixes]
    if len(out) > _MAX_ALTERNATIVES:
        return [(_slot({v for seq in out for values, _o, _s in seq for v in values}, splice=True),)]
    return out


def _one_of(values):
    """*values* of which one, unknown which, is taken."""
    return {v for v in values if v is not _EACH and not isinstance(v, _Certain)}


def _unresolved(values):
    return any(v is _UNKNOWN or isinstance(v, _Ref) for v in values)


def _has_refs(seq):
    return any(isinstance(v, _Ref) for values, _o, _s in seq for v in values)


class _Reach:
    """What a test's dispatch sites do with registry keys.

    run    keys proven to run: the one word in command position
    may    keys that might run: a read-only lookup, a word after the
           command, one of several alternatives
    every  every registry entry runs in turn
    any    an unresolved key or program: any handler might run
    unrelated  an allowlisted program started: unrelated only where the
           test leaves the environment unwritten
    """

    def __init__(self):
        self.run, self.may = set(), set()
        self.every = self.any = self.unrelated = False

    def update(self, other):
        self.run |= other.run
        self.may |= other.may
        self.every |= other.every
        self.any |= other.any
        self.unrelated |= other.unrelated

    def key(self, values, proven):
        if proven:
            self.run.update(v.value for v in values if isinstance(v, _Certain))
        each = _EACH in values
        values = _one_of(values)
        strings = {v for v in values if isinstance(v, str)}
        exact = proven and (each or len(values) == 1)
        (self.run if exact else self.may).update(strings)
        if _EVERY in values:
            if exact:
                self.every = True
            else:
                self.any = True
        if _unresolved(values):
            self.any = True

    def argument(self, values):
        """A word after the command: a key there may still be run (``help alpha``)."""
        self.may.update(v for v in values if isinstance(v, str))
        if _EVERY in values:
            self.any = True


def _option_arity(token, arity):
    """Words an option consumes after itself, or None when unknown."""
    if token == "--" or (token.startswith("--") and "=" in token):
        return 0
    return arity.get(token)


def _reach_argv(seq, arity, proven, reach):
    """Read *seq* as a group's argv: global options with their values, then the command.

    *arity* maps the group's options to the words they consume.  Every
    reading of the argv is followed (an option of unknown arity, an
    optional or unresolved word, a splice); a command is proven only when
    every reading puts the same literal in command position.
    """
    positions, tails, todo, done = set(), set(), [0], set()
    while todo:
        i = todo.pop()
        if i in done or i >= len(seq):
            continue
        done.add(i)
        values, optional, splice = seq[i]
        if splice:
            tails.add(i)
            continue
        if optional:
            todo.append(i + 1)
        values = _one_of(values)
        options = {v for v in values if isinstance(v, str) and v.startswith("-")}
        if values - options:
            positions.add(i)
        if _unresolved(values):
            todo.extend((i + 1, i + 2))  # an unresolved word may be an option and its value
        for option in options:
            a = _option_arity(option, arity)
            todo.extend(i + 1 + step for step in ((0, 1) if a is None else (a,)))
    exact = proven and len(positions) == 1 and not tails
    for pos in positions:
        values, optional, _splice = seq[pos]
        plain = _one_of(values)
        command = {v for v in plain if not (isinstance(v, str) and v.startswith("-"))}
        reach.key(command | (values - plain), exact and not optional and command == plain)
        for later, _o, _s in seq[pos + 1 :]:
            reach.argument(later)
    for i in tails:
        for later, _o, _s in seq[i:]:
            reach.key(later, False)


# Programs that provably never start the package: each runs only its own
# code and executes no program or code its arguments name.  Every other
# program (git, python, an unknown executable, a script, a wrapper such as
# env, timeout, nohup, uv, uvx, pdm) may start it, so it keeps possible reach.
# A name is trusted only bare or under /bin or /usr/bin, and only where the
# test writes no environment variable (PATH finds it, LD_PRELOAD loads code
# into it).
_UNRELATED_PROGRAMS = frozenset(
    {
        "echo",  # prints its arguments
        "true",  # exits 0
        "false",  # exits 1
        "ls",  # lists directories
        "cat",  # copies files to stdout
        "sleep",  # waits
        "pwd",  # prints the working directory
        "mkdir",  # creates directories
        "touch",  # updates timestamps
    }
)
_ENVIRON_NAMES = frozenset({"environ", "environb", "os.environ", "os.environb"})
_ENVIRON_READS = frozenset({"get", "copy", "keys", "values", "items"})
_ENV_SETTERS = frozenset({"setenv", "delenv", "putenv", "unsetenv"})
_ENV_KEY_READS = frozenset({"get", "getenv", "getenvb", "__getitem__", "__contains__"})
# Values that are a new mapping, never the environment itself.
_FRESH_MAPPINGS = frozenset({"dict", "copy.copy", "copy.deepcopy"})


def _program_name(value):
    base = re.split(r"[\\/]", value.strip())[-1].lower()
    return base[: -len(".exe")] if base.endswith(".exe") else base


def _program(value, programs):
    """Classify a package entry point, interpreter, shell, allowlisted or unknown program."""
    if value is _PYTHON:
        return "python"
    if not isinstance(value, str):
        return None
    base = _program_name(value)
    if re.fullmatch(r"python[\d.]*w?|py", base):
        return "python"
    if base in _SHELLS:
        return "shell"
    if base in programs:
        return "package"
    m = re.fullmatch(r"(?:/(?:usr/)?bin/)?([a-z]+)", value)
    return "unrelated" if m and m.group(1) in _UNRELATED_PROGRAMS else "unknown"


def _env_key(node):
    """Whether *node* is a literal shaped like an environment variable name (``PATH``, ``LD_PRELOAD``)."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value.upper() == "PATH" or re.fullmatch(r"[A-Z_][A-Z0-9_]*", node.value) is not None)
    )


def _fresh_names(tree, parents):
    """Names every binding of which is a new mapping (a dict display or copy), never the environment."""
    fresh, other = set(), set()

    def is_fresh(value):
        if isinstance(value, (ast.Dict, ast.DictComp)):
            return True
        if isinstance(value, ast.Call):
            func = ast.unparse(value.func)
            return func in _FRESH_MAPPINGS or (isinstance(value.func, ast.Attribute) and value.func.attr == "copy")
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            use = parents.get(node)
            ok = isinstance(use, (ast.Assign, ast.AnnAssign)) and node is not use.value and is_fresh(use.value)
            (fresh if ok and not (isinstance(use, ast.Assign) and node not in use.targets) else other).add(node.id)
        elif isinstance(node, ast.arg):
            other.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            other |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            other |= set(node.names)
    return fresh - other


def _environ_aliases(tree):
    """Names that may be the environment or one of its setters, resolved through imports.

    ``import os as X`` needs no entry: any ``.environ`` attribute counts.
    """
    environ, setters = {"environ", "environb"}, set(_ENV_SETTERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in ("environ", "environb", "*"):
                    environ.add(alias.asname or alias.name)
                elif alias.name in _ENV_SETTERS:
                    setters.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) for t in node.targets):
            value = node.value
            name = value.attr if isinstance(value, ast.Attribute) else value.id if isinstance(value, ast.Name) else None
            if name in _ENV_SETTERS:  # ``setenv = monkeypatch.setenv``
                setters |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return environ, setters


def _writes_environment(tree):
    """Whether code may write the process environment, to any key: an unresolved write counts, a read does not.

    Any variable changes what a launched program runs (``PATH``,
    ``LD_PRELOAD``, ``GIT_*``, ...), so no key is exempt.  The
    environment is any ``.environ`` attribute, any name an import from
    ``os`` binds it to, and any string naming it (``mock.patch.dict("os.environ")``,
    ``getattr(os, "environ")``).  A write with a literal key shaped like a
    variable name counts on any receiver not resolved as a new mapping.
    """
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    environ, setters = _environ_aliases(tree)
    fresh = _fresh_names(tree, parents)

    def unresolved(receiver):
        return not (isinstance(receiver, ast.Name) and receiver.id in fresh)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in _ENVIRON_NAMES:
            return True  # the environment named by a string: ``patch.dict("os.environ", ...)``
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in _ENV_SETTERS or (isinstance(func, ast.Name) and func.id in setters):
                return True  # ``monkeypatch.setenv(...)``, ``put(key, ...)``, ``os.unsetenv(...)``
            if name not in _ENV_KEY_READS and any(_env_key(a) for a in node.args):
                if not (isinstance(func, ast.Attribute) and not unresolved(func.value)):
                    return True  # ``setter("LD_PRELOAD", ...)``, ``target.setdefault("PATH")``
            if name == "update" and isinstance(func, ast.Attribute) and unresolved(func.value):
                keys = [None if k.arg is None else ast.Constant(k.arg) for k in node.keywords]
                keys += [
                    k if isinstance(k, ast.Constant) else None
                    for a in node.args
                    if isinstance(a, ast.Dict)
                    for k in a.keys
                ]
                if any(k is None or _env_key(k) for k in keys):
                    return True  # ``target.update(PATH=...)``, ``target.update({"LD_PRELOAD": ...})``
        if isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load) and _env_key(node.slice):
            if unresolved(node.value):
                return True  # ``target["LD_PRELOAD"] = ...`` on a receiver that may be the environment
        if isinstance(node, ast.Attribute):
            if node.attr not in ("environ", "environb"):
                continue
        elif not (isinstance(node, ast.Name) and node.id in environ):
            continue
        if not isinstance(node.ctx, ast.Load):
            return True  # ``os.environ = ...``
        use = parents.get(node)
        if isinstance(use, ast.Subscript) and use.value is node:
            if not isinstance(use.ctx, ast.Load):
                return True  # ``os.environ[key] = ...``, ``del os.environ[key]``
        elif isinstance(use, ast.Attribute) and use.value is node:
            if use.attr not in _ENVIRON_READS:
                return True  # ``setdefault``, ``pop``, ``update``, ``clear``, ...
        elif (
            isinstance(use, ast.Compare)
            or (isinstance(use, ast.Call) and node in use.args and ast.unparse(use.func) == "dict")
            or (isinstance(use, ast.Dict) and any(k is None and v is node for k, v in zip(use.keys, use.values)))
        ):
            continue  # ``"X" in os.environ``; ``dict(os.environ, ...)``, ``{**os.environ}`` copy it
        else:
            return True  # passed on (``mock.patch.dict(os.environ, ...)``) or aliased
    return False


def _launches(tree, qualnames):
    """Whether code names a process launcher (``subprocess.run``, ``os.system``, ...), called or not."""

    def dotted(e):
        if isinstance(e, ast.Name):
            return qualnames.get(e.id)
        if isinstance(e, ast.Attribute):
            base = dotted(e.value)
            return f"{base}.{e.attr}" if base else None
        return None

    modules = {name.rpartition(".")[0] for name in _PROCESS_CALLS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in modules and any(a.name == "*" for a in node.names):
            return True
        if isinstance(node, (ast.Name, ast.Attribute)) and dotted(node) in _PROCESS_CALLS:
            return True
    return False


def _shell_launch(call, qualname):
    """Whether a launch runs its command through a shell (``os.system``, ``shell=`` not literally false)."""
    if qualname in _PROCESS_SHELL:
        return True
    return any(
        k.arg == "shell" and not (isinstance(k.value, ast.Constant) and not k.value.value) for k in call.keywords
    )


def _names_environment(call, qualname):
    """Whether a launch passes its environment or executable, or arguments that may hold them."""
    if any(k.arg in (None, "env", "executable") for k in call.keywords):
        return True
    varargs = qualname in _PROCESS_VARARGS
    return any(isinstance(a, ast.Starred) for a in call.args) if varargs else len(call.args) > 1


def _reach_exec(seq, reach, programs, modules, arity, depth=0, dispatch_modules=frozenset(), trusted=True):
    """Reach of a process argv: ``roam ...``, ``python -m roam ...`` or any other program.

    *programs* are the package's executable names, *modules* what
    ``python -m`` runs as the package, *dispatch_modules* the modules
    whose import in ``python -c`` code may start its command line.  An
    unresolved program may be any of those, so its argv is read as each.
    Only the package is read as a command line; an allowlisted program is
    excluded when *trusted* (the launch names no environment or executable
    and the test writes no environment variable); every other program keeps
    possible reach.
    """
    if not seq:
        return
    values, _optional, splice = seq[0]
    if splice:
        for later, _o, _s in seq:
            reach.key(later, False)
        reach.any = True  # any word of the splice may be the program
        return
    values = _one_of(values)
    proven = len(values) == 1
    for v in values:
        if v is _UNKNOWN or v is _PATH or isinstance(v, _Ref):
            _reach_argv(seq[1:], arity, False, reach)
            reach.any = True
            continue
        kind = _program(v, programs)
        if kind == "package":
            _reach_argv(seq[1:], arity, proven, reach)
        elif kind == "python":
            _reach_python(seq[1:], reach, programs, modules, arity, proven, dispatch_modules)
        elif kind == "unrelated" and trusted:
            reach.unrelated = True  # unrelated unless the test writes the environment it runs in
        else:
            reach.any = True  # a shell, script, wrapper, interpreter or unknown program may start the package


def _reach_python(seq, reach, programs, modules, arity, proven, dispatch_modules=frozenset()):
    """Interpreter options, then ``-m <module>`` naming the package.

    Scripts, stdin, code and arbitrary modules retain possible reach to
    every command.
    """
    i = 0
    while i < len(seq):
        values, optional, splice = seq[i]
        values = _one_of(values)
        strings = {v for v in values if isinstance(v, str)}
        if splice or len(strings) != len(values):
            # The unresolved word may itself be a script path or code option.
            reach.any = True
            return
        if strings == {"-m"}:
            if i + 1 < len(seq):
                m_values, m_optional, m_splice = seq[i + 1]
                m_strings = {v for v in m_values if isinstance(v, str)}
                if m_splice or len(m_strings) != len(m_values):
                    reach.any = True
                elif m_strings - modules:
                    reach.any = True
                elif m_strings & modules:
                    exact = proven and not (optional or m_optional) and len(m_strings) == 1
                    _reach_argv(seq[i + 2 :], arity, exact, reach)
            return
        if "-c" in strings or "-m" in strings:
            reach.any = True  # code or a module that may import or launch a dispatcher
            return
        if strings & {"-X", "-W"}:
            i += 2
        elif all(s.startswith("-") and s != "-" for s in strings):
            i += 1
        else:
            reach.any = True  # a script (or ``-``, stdin) can launch any command
            return
    reach.any = True  # no script: the interpreter runs its stdin


def _split_word(token, fills):
    m = re.fullmatch(f"{_PH_OPEN}(\\d+){_PH_CLOSE}", token)
    if m:
        values = fills[int(m.group(1))]
        return _slot(values, splice=_UNKNOWN in values)
    if _PH_OPEN in token:
        head = token.split(_PH_OPEN, 1)[0]
        return _slot({head} if head.startswith("-") and "=" in head else {_UNKNOWN})
    return _slot({token})


def _split_sequences(parts):
    """The argv of a command string split as ``shlex.split`` does (``CliRunner.invoke``, ``shlex.split``).

    *parts* are literal text and the value sets of interpolated
    expressions: a word that is one interpolation takes its values, a
    word mixing text and an unresolved interpolation is unresolved.  No
    shell reads the string, so ``;``, ``&&``, ``|`` and ``>`` are words.
    """
    text, fills = "", []
    for part in parts:
        if isinstance(part, str):
            text += part.replace(_PH_OPEN, "").replace(_PH_CLOSE, "")
        elif len(part) == 1 and isinstance(next(iter(part)), str):
            text += next(iter(part))
        else:
            text += f"{_PH_OPEN}{len(fills)}{_PH_CLOSE}"
            fills.append(frozenset(part))
    try:
        words = shlex.split(text)
    except ValueError:
        return [(_slot({_UNKNOWN}, splice=True),)]
    return [tuple(_split_word(w, fills) for w in words)]


def _declared_options(decorators):
    """``{option: words consumed}`` from click ``option`` / ``version_option`` decorators."""
    arity = {"--help": 0, "-h": 0}
    for dec in decorators:
        if not isinstance(dec, ast.Call):
            continue
        name = ast.unparse(dec.func)
        if name.endswith("version_option"):
            arity["--version"] = 0
            continue
        if not name.endswith("option"):
            continue
        keywords = {k.arg: k.value for k in dec.keywords if k.arg}
        flag = any(
            k in keywords and not (isinstance(keywords[k], ast.Constant) and not keywords[k].value)
            for k in ("is_flag", "count", "flag_value")
        )
        nargs = keywords.get("nargs")
        consumed = nargs.value if isinstance(nargs, ast.Constant) and isinstance(nargs.value, int) else 1
        for a in dec.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                for token in a.value.split("/"):
                    token = token.strip()
                    if token.startswith("-"):
                        arity[token] = 0 if flag or "/" in a.value else consumed
    return arity


def _is_root(fn):
    """Tests, fixtures and setup hooks run on their own; helpers run when called."""
    if fn.name.startswith(("test", "setup", "setUp", "teardown", "tearDown")):
        return True
    return any("fixture" in ast.unparse(d) for d in fn.decorator_list)


def _fixture_name(fn):
    """The name pytest requests *fn* by when it is a fixture (``name=`` or its own), else None."""
    for d in fn.decorator_list:
        if "fixture" not in ast.unparse(d):
            continue
        for k in d.keywords if isinstance(d, ast.Call) else ():
            if k.arg == "name" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                return k.value.value
        return fn.name
    return None


def _autouse(fn):
    return any(
        isinstance(d, ast.Call)
        and "fixture" in ast.unparse(d.func)
        and any(
            k.arg == "autouse" and not (isinstance(k.value, ast.Constant) and not k.value.value) for k in d.keywords
        )
        for d in fn.decorator_list
    )


def _requested(fn):
    """Fixture names *fn* requests through its parameters."""
    args = fn.args
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)} - _SKIP_PARAMS


def _scope_fixtures(body):
    """``{name: [fn]}`` of the fixtures defined directly in *body* (a module or class body)."""
    found = {}
    for fn in body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _fixture_name(fn) is not None:
            found.setdefault(_fixture_name(fn), []).append(fn)
    return found


def _direct_parameters(decorators):
    """Names ``parametrize`` binds directly; these replace a fixture of that name, ``indirect`` ones do not."""
    names = set()
    for dec in decorators:
        if not (isinstance(dec, ast.Call) and ast.unparse(dec.func).endswith("parametrize") and dec.args):
            continue
        node = dec.args[0]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            bound = {n.strip() for n in node.value.split(",") if n.strip()}
        elif isinstance(node, (ast.List, ast.Tuple)):
            bound = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        else:
            continue
        for k in dec.keywords:
            if k.arg != "indirect":
                continue
            if isinstance(k.value, ast.Constant) and not k.value.value:
                continue
            if isinstance(k.value, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in k.value.elts):
                bound -= {e.value for e in k.value.elts}
            else:
                bound = set()  # indirect for all, or for names not read here: the fixtures still run
        names |= bound
    return names


def _parametrize_bindings(decorators):
    """``{argname: [value_expr | ("iter", expr)]}`` from ``pytest.mark.parametrize`` decorators."""
    bound = {}
    for dec in decorators:
        if not (isinstance(dec, ast.Call) and ast.unparse(dec.func).endswith("parametrize") and len(dec.args) >= 2):
            continue
        names_node, values = dec.args[0], dec.args[1]
        if isinstance(names_node, ast.Constant) and isinstance(names_node.value, str):
            names = [n.strip() for n in names_node.value.split(",") if n.strip()]
        elif isinstance(names_node, (ast.List, ast.Tuple)):
            names = [e.value for e in names_node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        else:
            continue
        if not isinstance(values, (ast.List, ast.Tuple)):
            for name in names:
                bound.setdefault(name, []).append(("iter", values))  # every item is a case
            continue
        for value in values.elts:
            if isinstance(value, ast.Call) and ast.unparse(value.func).endswith("param"):
                items = value.args
            elif len(names) > 1 and isinstance(value, (ast.List, ast.Tuple)):
                items = value.elts
            else:
                items = [value]
            for name, item in zip(names, items):
                bound.setdefault(name, []).append(item)
    return bound


def _walk_own_body(node):
    """``ast.walk`` that does not enter nested functions, lambdas or classes."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                stack.append(child)


_LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.comprehension)
_BRANCHES = (ast.If, ast.IfExp, ast.BoolOp, ast.ExceptHandler, getattr(ast, "match_case", ast.If))


def _unconditional_site(node, site):
    """Whether the site is outside conditional expressions and nested loops."""
    if node is site:
        return True
    if isinstance(node, _BRANCHES + _LOOPS + (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
        return False
    return any(_unconditional_site(child, site) for child in ast.iter_child_nodes(node))


def _loop_exit_state(iterable, site, may_stop=lambda call: False):
    """Return early exits and whether the use precedes all of them unconditionally.

    An exit is ``break``, ``continue``, ``return``, ``raise`` or a call
    *may_stop* cannot prove returns.  Inside ``try`` / ``pytest.raises``
    any call may leave the loop while the test passes, so no value is
    certain.
    """
    loop = getattr(iterable, "roam_loop", None)
    if loop is None:
        return [], False
    if getattr(iterable, "roam_guarded", False):
        return [loop], False
    exits = [
        n
        for n in _walk_own_body(loop)
        if isinstance(n, (ast.Break, ast.Continue, ast.Return, ast.Raise)) or (isinstance(n, ast.Call) and may_stop(n))
    ]
    if not exits:
        return exits, False
    prefix = loop.body[
        : next(
            (i for i, stmt in enumerate(loop.body) if any(x in set(ast.walk(stmt)) for x in exits)),
            len(loop.body),
        )
    ]
    return exits, any(_unconditional_site(stmt, site) for stmt in prefix)


def _ancestors(node, parents):
    parent = parents.get(node)
    while parent is not None:
        yield parent
        parent = parents.get(parent)


def _swallows(node):
    """A ``try`` with handlers or a ``with`` that catches: a call inside may raise and the test still pass."""
    if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
        return bool(node.handlers)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return any(
            ast.unparse(item.context_expr.func if isinstance(item.context_expr, ast.Call) else item.context_expr)
            .rsplit(".", 1)[-1]
            .startswith(_SWALLOWERS)
            for item in node.items
        )
    return False


def _function_facts(fn):
    """``{name: [(kind, value, flag)]}``: the names *fn* binds, in source order.

    kind is ``assign``, ``unpack`` (one of several targets), ``iter`` (a
    loop target), ``opaque``, or a list mutation ``append`` / ``extend``
    / ``insert`` (at the front) / ``insert_any``.  flag is ``"loop"``
    under a loop, ``"cond"`` under a branch, else None.
    """
    parents = {}
    for node in _walk_own_body(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def flag(node):
        found = None
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, _LOOPS):
                return "loop"
            if isinstance(parent, _BRANCHES):
                found = "cond"
            parent = parents.get(parent)
        return found

    events = []

    def add(target, kind, value, node):
        if isinstance(target, ast.Name):
            events.append((node.lineno, node.col_offset, target.id, kind, value, flag(node)))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                add(elt, "unpack" if kind == "assign" else kind, value, node)
        elif isinstance(target, ast.Starred):
            add(target.value, kind, value, node)

    for node in _walk_own_body(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                add(target, "assign", node.value, node)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            add(node.target, "assign", node.value, node)
        elif isinstance(node, ast.AugAssign):
            add(node.target, "extend", node.value, node)
        elif isinstance(node, ast.NamedExpr):
            add(node.target, "assign", node.value, node)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            node.iter.roam_loop = node
            node.iter.roam_guarded = any(_swallows(n) for n in (*_ancestors(node, parents), *_walk_own_body(node)))
            add(node.target, "iter", node.iter, node)
        elif isinstance(node, ast.comprehension):
            add(node.target, "iter", node.iter, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            add(node.optional_vars, "opaque", None, node.context_expr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend", "insert")
            and isinstance(node.func.value, ast.Name)
            and node.args
        ):
            kind = node.func.attr
            if kind == "insert":
                front = len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0
                kind = "insert" if front else "insert_any"
            events.append((node.lineno, node.col_offset, node.func.value.id, kind, node.args[-1], flag(node)))
    by_name = {}
    for _line, _col, name, kind, value, fl in sorted(events, key=lambda e: (e[0], e[1])):
        by_name.setdefault(name, []).append((kind, value, fl))
    return by_name


def _invocation_args(call, start=0):
    """Arguments of a click-style invocation: positionals from *start*, plus ``args=``."""
    return [*call.args[start:], *(k.value for k in call.keywords if k.arg == "args")]


class _Summary:
    """A function's dispatch: its settled reach and the templates its callers complete.

    ``pending`` holds ``(mode, argv, arity)`` templates that still name a
    parameter, and every registry read (a caller that runs the returned
    entry turns the read into a run).
    """

    __slots__ = ("reach", "pending")

    def __init__(self):
        self.reach = _Reach()
        self.pending = {}


class _DispatchScope:
    """Follow test code to the dispatch sites of keyed registries.

    *flood* holds ``(path, name)`` of every symbol that reaches a keyed
    registry through reverse edges; *registries* maps ``(path, name)``
    to that registry's keys.  Names bind through each file's own
    imports, so ``from roam import cli`` and ``from roam.cli import cli``
    resolve alike, with or without an edge from the test.  Helpers are
    summarised once, their parameters bound per call.
    """

    def __init__(self, root, module_paths, flood, registries):
        self.root = root
        self.module_paths = module_paths
        self.flood = flood
        self.registries = registries
        self._files = {}
        self._summaries = {}
        self._facts = {}
        self._arity = {}
        self._group_arity = None
        self._returning = set()
        self._registry_readers = {}
        self._stops = {}
        packages = {_python_module_name(p).split(".")[0] for p, _n in registries}
        self.programs = packages
        self.modules = {*packages, *(f"{p}.__main__" for p in packages)}
        self.dispatch_modules = {_python_module_name(p) for p, _n in [*flood, *registries]}

    # -- names ------------------------------------------------------------

    def file(self, path):
        if path in self._files:
            return self._files[path]
        tree = _parse_python(self.root, path)
        info = None
        if tree is not None:
            info = {"imports": {}, "qualnames": {}, "globals": {}, "functions": {}}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            info["globals"].setdefault(t.id, []).append(node.value)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                    info["globals"].setdefault(node.target.id, []).append(node.value)
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            item.roam_class_decorators = node.decorator_list

            def enclosures(node, outer=None):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        child.roam_enclosing = outer
                        enclosures(child, child)
                    else:
                        enclosures(child, outer)

            enclosures(tree)
            info["rebound_globals"] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    node.roam_rebound = getattr(node, "roam_rebound", set())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info["functions"].setdefault(node.name, []).append(node)
                    own = list(_walk_own_body(node))
                    written = {s.id for s in own if isinstance(s, ast.Name) and not isinstance(s.ctx, ast.Load)}
                    # A write through ``global`` makes the module value unknown;
                    # one through ``nonlocal``, every enclosing value.
                    info["rebound_globals"] |= {
                        n for s in own if isinstance(s, ast.Global) for n in s.names if n in written
                    }
                    outer = getattr(node, "roam_enclosing", None)
                    nonlocal_writes = {n for s in own if isinstance(s, ast.Nonlocal) for n in s.names if n in written}
                    while outer is not None and nonlocal_writes:
                        outer.roam_rebound = getattr(outer, "roam_rebound", set()) | nonlocal_writes
                        outer = getattr(outer, "roam_enclosing", None)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        bound = alias.asname or alias.name.split(".")[0]
                        info["imports"][bound] = ("module", alias.name if alias.asname else bound)
                        info["qualnames"][bound] = alias.name if alias.asname else bound
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    for alias in node.names:
                        info["imports"][alias.asname or alias.name] = self._member(node.module, alias.name)
                        info["qualnames"][alias.asname or alias.name] = f"{node.module}.{alias.name}"
            info["module"] = self._pseudo_function("<module>", tree.body, tree)
            info["writes_environment"] = _writes_environment(tree)
            info["loads"], info["plugins"] = self._loads(tree, path)
            info["launches"] = _launches(tree, info["qualnames"])
        self._files[path] = info
        return info

    @staticmethod
    def _pseudo_function(name, body, node, args=None):
        """A ``def`` standing for module-level code or a lambda body, analysed like a function."""
        empty = ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None)
        fn = ast.FunctionDef(name=name, args=args or empty, body=body, decorator_list=[], returns=None)
        fn.lineno, fn.col_offset = getattr(node, "lineno", 0), getattr(node, "col_offset", -1)
        fn.type_params = []
        return fn

    def writes_environment(self, path):
        """Whether the module at *path* may write the environment; an unreadable one may."""
        info = self.file(path)
        return info is None or info["writes_environment"]

    def _loads(self, tree, path):
        """Project modules *tree* imports (with their packages) and names in ``pytest_plugins``.

        A ``pytest_plugins`` value that is not a literal loads an unknown module: ``None``.
        """
        module = _python_module_name(path) or ""
        package = module if path.replace("\\", "/").endswith("/__init__.py") else module.rpartition(".")[0]
        names, plugins = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".") if package else []
                    parts = parts[: len(parts) - (node.level - 1)] if node.level - 1 <= len(parts) else []
                    base = ".".join([*parts, *([base] if base else [])])
                names.add(base)
                names.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else []
            if not any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                continue
            value = node.value
            items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
            for item in items:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    plugins.add(item.value)
                else:
                    plugins = None
                    break
            if plugins is None:
                break

        def local(modules):  # importing ``a.b`` runs ``a`` first
            parts = [m.split(".") for m in modules]
            dotted = {".".join(p[:i]) for p in parts for i in range(1, len(p) + 1)}
            return {self.module_paths[m] for m in dotted if m in self.module_paths}

        return local(names), None if plugins is None else local(plugins)

    def loaded_effects(self, path, conftests):
        """``(launches, writes_environment)`` of project code a test module loads besides itself.

        The code is the conftests that apply, the modules named in their or
        the test's ``pytest_plugins`` (read like conftests), and the project
        modules any of these import, followed up to ``_LOADED_DEPTH`` import
        hops.  The package that owns the registry is production code and is
        not followed.  An environment write anywhere there counts like one in
        the test; a process start in any loaded module, test-side helpers
        included, keeps possible reach, as its argv is not read there (code
        run on import, a method of a class defined there).  An unreadable module or plugin list may do
        either.  Neither ever proves coverage.
        """
        launches = writes = False
        seen = {path}
        frontier = []
        for start in (path, *conftests):
            info = self.file(start)
            if info is None:
                return True, True
            if start != path:
                seen.add(start)
                writes |= info["writes_environment"]
            if info["plugins"] is None:
                return True, True
            frontier += [*info["loads"], *info["plugins"]]
        for _depth in range(_LOADED_DEPTH):
            following = []
            for module in frontier:
                if module in seen or (_python_module_name(module) or "").split(".")[0] in self.programs:
                    continue
                seen.add(module)
                info = self.file(module)
                if info is None or info["plugins"] is None:
                    return True, True
                writes |= info["writes_environment"]
                launches |= info["launches"]
                following += [*info["loads"], *info["plugins"]]
            frontier = following
        return launches, writes

    def release(self, path):
        """Drop a processed test module's trees; a later reference parses it again.

        Shared helpers (``conftest.py``, ``helpers.py``) stay cached.
        """
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if not (name.startswith("test_") or name.endswith("_test.py")):
            return
        self._files.pop(path, None)
        for cache in (self._summaries, self._facts):
            for key in [k for k in cache if k[0] == path]:
                del cache[key]

    def _member(self, module, name):
        if f"{module}.{name}" in self.module_paths:
            return ("module", f"{module}.{name}")
        path = self.module_paths.get(module)
        if path is None:
            return None
        if (path, name) in self.registries:
            return ("registry", (path, name))
        return ("function", (path, name))

    def target(self, expr, path):
        """Binding of a Name / Attribute chain in *path*, else None."""
        info = self.file(path)
        if info is None:
            return None
        if isinstance(expr, ast.Name):
            if (path, expr.id) in self.registries:
                return ("registry", (path, expr.id))
            if expr.id in info["functions"]:
                return ("function", (path, expr.id))
            return info["imports"].get(expr.id)
        if isinstance(expr, ast.Attribute):
            if isinstance(expr.value, ast.Name) and expr.value.id in _SKIP_PARAMS:
                return ("function", (path, expr.attr)) if expr.attr in info["functions"] else None
            base = self.target(expr.value, path)
            if base and base[0] == "module":
                return self._member(base[1], expr.attr)
        return None

    def qualname(self, expr, path):
        """Dotted name of an imported object (``subprocess.run``), else None."""
        if isinstance(expr, ast.Name):
            info = self.file(path)
            return info["qualnames"].get(expr.id) if info else None
        if isinstance(expr, ast.Attribute):
            base = self.qualname(expr.value, path)
            return f"{base}.{expr.attr}" if base else None
        return None

    def is_registry(self, expr, path):
        """The registry itself or a view of it (``sorted(R)``, ``R.items()``)."""
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Attribute) and expr.func.attr in ("items", "keys", "values"):
                return self.is_registry(expr.func.value, path)
            return any(self.is_registry(a, path) for a in expr.args)
        t = self.target(expr, path)
        return bool(t and t[0] == "registry")

    def is_lookup(self, expr, path):
        """``R[k]`` / ``dict(R)[k]`` / ``R.get(k)``: a key lookup, not iteration."""
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "dict":
            return any(self.is_lookup(a, path) for a in expr.args)
        t = self.target(expr, path)
        return bool(t and t[0] == "registry")

    def definition(self, t):
        """Follow a ``function`` binding through re-exports to its ``def``, else None."""
        for _ in range(5):
            if not (t and t[0] == "function"):
                return None
            info = self.file(t[1][0])
            if info is None:
                return None
            if t[1][1] in info["functions"]:
                return t
            t = info["imports"].get(t[1][1])
        return None

    def dispatcher(self, t):
        if not (t and t[0] == "function"):
            return False
        d = self.definition(t)
        return t[1] in self.flood or bool(d and d[1] in self.flood)

    def readable(self, t):
        """The ``def`` behind *t* when its body may decide keys (reaches a registry or is test-side)."""
        d = self.definition(t)
        if d and (self.dispatcher(t) or is_test_file(d[1][0])):
            return d
        if d:
            if d not in self._registry_readers:
                self._registry_readers[d] = any(
                    (isinstance(node, ast.Subscript) and self.is_lookup(node.value, d[1][0]))
                    or (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get"
                        and self.is_lookup(node.func.value, d[1][0])
                    )
                    for fn in self._defs(d)
                    for node in _walk_own_body(fn)
                )
            if self._registry_readers[d]:
                return d
        return None

    def _defs(self, d):
        info = self.file(d[1][0])
        return info["functions"].get(d[1][1], []) if info else []

    def click_kind(self, t):
        """``"group"`` / ``"command"`` for a click object that reaches a registry, else None."""
        if not self.dispatcher(t):
            return None
        d = self.definition(t)
        if d is None:
            return "group"
        text = " ".join(ast.unparse(dec) for fn in self._defs(d) for dec in fn.decorator_list)
        if "group" in text:
            return "group"
        return "command" if "command" in text else None

    def arity(self, t):
        """``{option: words consumed}`` declared on the click group behind *t*."""
        d = self.definition(t)
        if d is None:
            return self.group_arity()
        if d[1] not in self._arity:
            arity = {}
            for fn in self._defs(d):
                arity.update(_declared_options(fn.decorator_list))
            self._arity[d[1]] = arity
        return self._arity[d[1]]

    def group_arity(self):
        """Options of the package's command-line group: what ``python -m <package>`` runs.

        Without a ``__main__`` module, every click group in the flood.
        """
        if self._group_arity is None:
            self._group_arity = {}
            groups = []
            for package in sorted(self.programs):
                main = self.module_paths.get(f"{package}.__main__")
                info = self.file(main) if main else None
                groups += [t for t in (info["imports"].values() if info else ()) if self.click_kind(t) == "group"]
            if not groups:
                groups = [("function", key) for key in sorted(self.flood)]
                groups = [t for t in groups if self._defines_group(t[1][0]) and self.click_kind(t) == "group"]
            for t in groups:
                self._group_arity.update(self.arity(t))
        return self._group_arity

    def _defines_group(self, path):
        try:
            return "group(" in (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    # -- reach --------------------------------------------------------------

    def reach_root(self, path, name):
        """Reach of every test, fixture or setup hook named *name* in *path*; None: module-level code."""
        reach = _Reach()
        info = self.file(path)
        if info is None:
            return reach
        roots = [info["module"]] if name is None else [fn for fn in info["functions"].get(name, ()) if _is_root(fn)]
        for fn in roots:
            reach.update(self._root_reach(fn, path))
        return reach

    def fixture_reach(self, path, conftests):
        """Reach of the conftest fixtures that run with *path*'s tests: requested by name or ``usefixtures``, or autouse.

        Each test resolves a name as pytest resolves it: its class bodies
        (and their same-module bases) first, then the test module, then the
        conftests from the nearest folder out; a fixture that requests its
        own name also runs the one it overrides.  Only fixtures a test reaches
        add their requests, and a name ``parametrize`` binds directly (not
        ``indirect``) replaces the fixture.  Fixtures in the test module are
        read as its own roots already.
        """
        reach = _Reach()
        info = self.file(path)
        if info is None:
            reach.any = True
            return reach
        conftest_scopes = []  # (path, {name: [fn]}), nearest first
        for p in sorted(conftests, key=lambda c: -c.replace("\\", "/").count("/")):
            conftest_info = self.file(p)
            if conftest_info is None:
                reach.any = True
                continue
            conftest_scopes.append((p, _scope_fixtures(conftest_info["module"].body)))
        marked = set()
        for node in ast.walk(info["module"]):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("usefixtures"):
                marked |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
                if not all(isinstance(a, ast.Constant) for a in node.args):
                    reach.any = True  # a fixture named by a computed value may be any of them
        classes = {n.name: n for n in info["module"].body if isinstance(n, ast.ClassDef)}

        def class_scopes(cls, seen=()):
            yield (None, _scope_fixtures(cls.body))
            for base in cls.bases:
                if isinstance(base, ast.Name) and base.id in classes and base.id not in seen:
                    yield from class_scopes(classes[base.id], (*seen, cls.name))

        def tests(body, outer):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    yield from tests(node.body, [*class_scopes(node), *outer])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    yield node, outer

        module_scope = [(None, _scope_fixtures(info["module"].body))]
        run = {}  # (path, lineno, col) -> (fn, path): conftest fixtures some test runs
        for test, outer in tests(info["module"].body, module_scope):
            scopes = [*outer, *conftest_scopes]
            decorators = [*test.decorator_list, *getattr(test, "roam_class_decorators", [])]
            blocked = _direct_parameters(decorators)
            todo = (_requested(test) | marked) - blocked
            todo |= {name for _p, found in scopes for name, fns in found.items() if any(map(_autouse, fns))}
            done = set(blocked)
            while todo:
                name = todo.pop()
                done.add(name)
                for p, found in scopes:
                    fns = found.get(name, ())
                    if not fns:
                        continue
                    if p is not None:
                        for fn in fns:
                            run[(p, fn.lineno, fn.col_offset)] = (fn, p)
                    requested = set().union(*map(_requested, fns))
                    todo |= requested - done - {name}
                    if name not in requested:
                        break
        for fn, p in run.values():
            reach.update(self._root_reach(fn, p))
        return reach

    def _root_reach(self, fn, path):
        reach = _Reach()
        summary = self._summary(fn, path, root=True)
        reach.update(summary.reach)
        for mode, seq, arity in summary.pending:
            for bound in self._substitute(seq, {}):  # an unbound parameter is unresolved
                self._evaluate(mode, bound, arity, reach)
        return reach

    def _summary(self, fn, path, root=False):
        key = (path, fn.lineno, fn.col_offset, root)
        if key not in self._summaries:
            self._summaries[key] = _Summary()  # recursion: the outer frame covers it
            self._summaries[key] = self._summarize(fn, path, root)
        return self._summaries[key]

    def _context(self, fn, path, root):
        key = (path, fn.lineno, fn.col_offset)
        if key not in self._facts:
            facts = _function_facts(fn)
            for name in getattr(fn, "roam_rebound", ()):  # a nested scope may rebind it at any call
                facts.setdefault(name, []).append(("opaque", None, None))
            self._facts[key] = facts
            fn.roam_global_names = {
                name for node in _walk_own_body(fn) if isinstance(node, ast.Global) for name in node.names
            }
        decorators = [*fn.decorator_list, *getattr(fn, "roam_class_decorators", [])]
        args = fn.args
        params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        params |= {a.arg for a in (args.vararg, args.kwarg) if a}
        return {
            "path": path,
            "enclosing": getattr(fn, "roam_enclosing", None),
            "globals": getattr(fn, "roam_global_names", set()),
            "events": self._facts[key],
            "params": params,
            "parametrized": _parametrize_bindings(decorators) if root else {},
            "root": root,
        }

    @staticmethod
    def _module_context(path):
        return {"path": path, "events": {}, "params": set(), "parametrized": {}, "root": False}

    def _summarize(self, fn, path, root):
        ctx = self._context(fn, path, root)
        test_side = is_test_file(path)
        summary = _Summary()
        invoked = self._invoked_calls(fn, ctx)
        registry_loops = []

        def add(mode, seqs, arity=None):
            for seq in seqs:
                self._settle(mode, seq, arity, summary, test_side)

        for node in _walk_own_body(fn):
            ctx["site"] = node
            if isinstance(node, (ast.For, ast.AsyncFor)) and self.is_registry(node.iter, path):
                registry_loops.append(node)
            elif (
                isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and self.is_lookup(node.value, path)
            ):
                add("read", [(_slot(self.words(node.slice, ctx)),)])
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get" and self.is_lookup(func.value, path):
                if node.args:
                    add("read", [(_slot(self.words(node.args[0], ctx)),)])
                continue
            qualname = self.qualname(func, path)
            if test_side and qualname in _PROCESS_OPAQUE:
                summary.reach.any = True
                continue
            if test_side and qualname in _PROCESS_CALLS:
                command = [*node.args[:1], *(k.value for k in node.keywords if k.arg in ("args", "cmd"))][:1]
                text = qualname not in _PROCESS_VARARGS and command and self._is_text(command[0], ctx)
                if _shell_launch(node, qualname) or text:
                    # A shell may run any program or skip the one it names (``false &&``, functions,
                    # quoting, PATH=, $(...)): its string is never read, neither to prove nor exclude.
                    summary.reach.any = True
                    continue
                trusted = not (self.writes_environment(path) or _names_environment(node, qualname))
                add("exec" if trusted else "exec_env", self._process_seqs(node, qualname, ctx))
                continue
            if test_side and isinstance(func, ast.Name) and self._launcher_alias(func.id, ctx):
                summary.reach.any = True  # ``run = subprocess.run``: the launch is not read
                continue
            t = self.target(func, path)
            if self.click_kind(t) == "group":  # cli([...])
                add("argv", self._argv_seqs(_invocation_args(node)[:1], ctx), self.arity(t))
                continue
            if isinstance(func, ast.Attribute) and func.attr == "main":
                group = self.target(func.value, path)
                if self.click_kind(group):  # cli.main([...])
                    add("argv", self._argv_seqs(_invocation_args(node)[:1], ctx), self.arity(group))
                    continue
            d = self.readable(t)
            if d:
                promoted = id(node) in invoked
                self._call(node, d, isinstance(func, ast.Attribute), ctx, summary, promoted, test_side)
                continue
            if "invoke" not in (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")):
                continue
            for i, arg in enumerate(node.args):
                group = self.target(arg, path)
                kind = self.click_kind(group)
                if kind:  # runner.invoke(cli, [...])
                    argv = self._argv_seqs(_invocation_args(node, i + 1)[:1], ctx)
                    if kind == "group":
                        add("argv", argv, self.arity(group))
                    else:
                        add("args", argv)
                    break

        if test_side:
            self._escapes(fn, path, summary.reach)

        # ``for name, (mod, fn) in R.items(): getattr(import_module(mod), fn)()``
        # calls the entry itself; ``mod.replace(".", "/")`` only reads it.
        for loop in registry_loops if test_side else ():
            loop_names = {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}
            for node in _walk_own_body(loop):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Name) and func.id in loop_names) or (
                    isinstance(func, (ast.Call, ast.Subscript))
                    and any(isinstance(n, ast.Name) and n.id in loop_names for n in ast.walk(func))
                ):
                    summary.reach.every = True
        return summary

    def _escapes(self, fn, path, reach):
        """Launchers and test code that run later, or elsewhere, than where they are named.

        A launcher passed on or aliased (``run = subprocess.run``,
        ``partial(subprocess.run, ...)``), a test-side function passed as a
        value (a callback) and a lambda body may each run: their reach is
        possible, never proven.
        """
        called = {id(n.func) for n in _walk_own_body(fn) if isinstance(n, ast.Call)}
        for node in _walk_own_body(fn):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Lambda):
                    if not hasattr(child, "roam_def"):
                        child.roam_def = self._pseudo_function("<lambda>", [ast.Return(child.body)], child, child.args)
                        child.roam_def.roam_enclosing = fn
                    self._fold_possible(self._summary(child.roam_def, path), reach)
            if id(node) in called or not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            if self.qualname(node, path) in _PROCESS_CALLS:
                reach.any = True
                continue
            d = self.readable(self.target(node, path))
            if d and is_test_file(d[1][0]):
                for callee in self._defs(d):
                    if callee is not fn:
                        self._fold_possible(self._summary(callee, d[1][0]), reach)

    def _fold_possible(self, sub, reach):
        """Add what *sub* reaches as possible reach: it may run, with unknown arguments."""
        possible = _Reach()
        possible.update(sub.reach)
        for mode, seq, arity in sub.pending:
            for bound in self._substitute(seq, {}):
                self._evaluate(mode, bound, arity, possible)
        reach.may |= possible.run | possible.may
        reach.any |= possible.any or possible.every
        reach.unrelated |= possible.unrelated

    def _launcher_alias(self, name, ctx):
        """Whether *name* is bound to an expression naming a process launcher."""
        sources = [(v, ctx) for k, v, _f in ctx["events"].get(name, ()) if k == "assign"]
        sources += self._global_sources(name, ctx) or []
        return any(
            isinstance(n, (ast.Name, ast.Attribute)) and self.qualname(n, c["path"]) in _PROCESS_CALLS
            for v, c in sources
            for n in ast.walk(v)
        )

    def _loop_exit_state(self, iterable, ctx):
        return _loop_exit_state(iterable, ctx.get("site"), lambda call: self._may_stop(call, ctx["path"]))

    def _may_stop(self, call, path, depth=0):
        """Whether *call* may end the test without failing it: a skip, an exit, or a helper that does."""
        func = call.func
        if (self.qualname(func, path) or ast.unparse(func)) in _TERMINATORS:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in _SKIP_PARAMS and func.attr in _SELF_TERMINATORS:
                return True
        d = self.definition(self.target(func, path))
        if depth >= 3 or d is None or not is_test_file(d[1][0]):
            return False
        if d not in self._stops:
            self._stops[d] = False  # recursion: the outer frame decides
            self._stops[d] = any(
                isinstance(n, ast.Call) and self._may_stop(n, d[1][0], depth + 1)
                for callee in self._defs(d)
                for n in _walk_own_body(callee)
            )
        return self._stops[d]

    def _invoked_calls(self, fn, ctx):
        """Calls whose result is itself run: ``f(...)()``, ``f(...).main()``, ``invoke(f(...))``."""
        out = set()
        for node in _walk_own_body(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Call):
                out.add(id(func))
            if isinstance(func, ast.Attribute) and func.attr in ("main", "invoke", "__call__"):
                if isinstance(func.value, ast.Call):
                    out.add(id(func.value))
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if "invoke" in name and node.args:
                first = node.args[0]
                if isinstance(first, ast.Call):
                    out.add(id(first))
                elif isinstance(first, ast.Name):
                    for kind, value, _flag in ctx["events"].get(first.id, ()):
                        if kind == "assign" and isinstance(value, ast.Call):
                            out.add(id(value))
        return out

    def _call(self, call, d, via_attribute, ctx, summary, promoted, test_side):
        """Follow a call into a helper or dispatcher with its parameters bound."""
        for fn in self._defs(d):
            sub = self._summary(fn, d[1][0])
            summary.reach.update(sub.reach)
            if not sub.pending:
                continue
            binding = self._bind(call, fn, via_attribute, ctx, d[1][0])
            for mode, seq, arity in sub.pending:
                if promoted and mode == "read":
                    mode = "run"
                for bound in self._substitute(seq, binding):
                    self._settle(mode, bound, arity, summary, test_side)

    def _settle(self, mode, seq, arity, summary, test_side):
        """Evaluate a template once no parameter is left; keep reads for a caller that may run them.

        Unknown production keys survive to the caller as possible reach.
        """
        if isinstance(arity, dict):
            arity = tuple(sorted(arity.items()))
        if mode == "read" or _has_refs(seq):
            key = (mode, seq, arity)
            if len(summary.pending) < _MAX_PENDING:
                summary.pending[key] = None
                return
            seq = self._substitute(seq, {})[0]
        self._evaluate(mode, seq, arity, summary.reach)

    def _evaluate(self, mode, seq, arity, reach):
        arity = dict(arity) if arity is not None else None
        if mode == "argv":
            _reach_argv(seq, arity, True, reach)
        elif mode == "args":
            for values, _o, _s in seq:
                reach.argument(values)
        elif mode in ("exec", "exec_env"):
            trusted = mode == "exec"
            _reach_exec(seq, reach, self.programs, self.modules, self.group_arity(), 0, self.dispatch_modules, trusted)
        else:  # "read" only inspects the entry; "run" runs it
            values = frozenset().union(*(v for v, _o, _s in seq))
            single = len(seq) == 1 and not seq[0][1] and not seq[0][2]
            reach.key(values, mode == "run" and single)

    # -- binding ------------------------------------------------------------

    def _bind(self, call, fn, via_attribute, ctx, callee_path):
        """``{param: binding}`` for *call* into *fn*; defaults bind in the callee's module."""
        args = fn.args
        positional = [a.arg for a in (*args.posonlyargs, *args.args)]
        if via_attribute and positional and positional[0] in _SKIP_PARAMS:
            positional = positional[1:]
        binding, extra = {}, []
        for i, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                for name in positional[i:]:
                    binding[name] = None
                extra.append(arg)
                break
            if i < len(positional):
                binding[positional[i]] = ("expr", arg, ctx)
            else:
                extra.append(arg)
        if args.vararg:
            binding[args.vararg.arg] = ("var", extra, ctx)
        for kw in call.keywords:
            if kw.arg is not None:
                binding[kw.arg] = ("expr", kw.value, ctx)
        module_ctx = self._module_context(callee_path)
        for arg, default in zip(reversed([*args.posonlyargs, *args.args]), reversed(args.defaults)):
            binding.setdefault(arg.arg, ("expr", default, module_ctx))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None:
                binding.setdefault(arg.arg, ("expr", default, module_ctx))
        return binding

    def _bound_words(self, name, binding):
        b = binding.get(name)
        if b is None:
            return {_UNKNOWN}
        kind, value, ctx = b
        if kind == "expr":
            return self.words(value, ctx)
        words = set().union(set(), *(self.words(x, ctx) for x in value))
        return words if len(value) == 1 else _one_of(words)

    def _bound_seqs(self, name, binding):
        b = binding.get(name)
        if b is None:
            return [(_slot({_UNKNOWN}, splice=True),)]
        kind, value, ctx = b
        return self.seqs(value, ctx) if kind == "expr" else self._seq_of(value, ctx)

    def _substitute(self, seq, binding):
        """Alternatives of *seq* with its parameters replaced by what *binding* passes."""
        out = [()]
        for values, optional, splice in seq:
            refs = [v for v in values if isinstance(v, _Ref)]
            if not refs:
                out = _concat(out, [((values, optional, splice),)])
                continue
            plain = values - set(refs)
            if splice and len(refs) == 1 and refs[0].seq and not plain:
                alternatives = self._bound_seqs(refs[0].name, binding)
                if optional:
                    alternatives = [tuple((v, True, s) for v, _o, s in alt) for alt in alternatives]
                out = _concat(out, alternatives)
                continue
            words = set(plain)
            for ref in refs:
                words |= self._bound_words(ref.name, binding)
            if _one_of(plain) and _EACH not in plain:
                words = _one_of(words)  # a caller's loop does not make this slot's other values each run
            out = _concat(out, [(_slot(words, optional, splice),)])
        return out

    def _substitute_words(self, values, binding):
        out = {v for v in values if not isinstance(v, _Ref)}
        for ref in values - out:
            out |= self._bound_words(ref.name, binding)
        return out

    def _returns(self, call, ctx):
        """``[(return_exprs, callee_ctx, binding)]`` of a test-side helper call, else None."""
        d = self.definition(self.target(call.func, ctx["path"]))
        if d is None or not is_test_file(d[1][0]):
            return None
        out = []
        for fn in self._defs(d):
            key = (d[1][0], fn.lineno, fn.col_offset)
            returns = [n.value for n in _walk_own_body(fn) if isinstance(n, ast.Return) and n.value is not None]
            if key in self._returning or not returns:
                continue
            binding = self._bind(call, fn, isinstance(call.func, ast.Attribute), ctx, d[1][0])
            out.append((key, returns, self._context(fn, d[1][0], False), binding))
        return out or None

    # -- values -------------------------------------------------------------

    def _param(self, name, ctx, seq):
        return {_UNKNOWN} if ctx["root"] else {_Ref(name, seq)}

    def words(self, e, ctx, seen=frozenset()):
        """Values one argv word may take."""
        if isinstance(e, ast.Constant):
            if isinstance(e.value, str):
                return {e.value}
            return set() if e.value is None else {str(e.value)}
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return _one_of(set().union(set(), *(self.words(x, ctx, seen) for x in e.elts)))
        if isinstance(e, ast.Starred):
            return self.elements(e.value, ctx, seen)
        if isinstance(e, ast.Dict):
            return _one_of(set().union(set(), *(self.words(k, ctx, seen) for k in e.keys if k is not None)))
        if isinstance(e, ast.Name):
            return self._name_words(e.id, ctx, seen)
        if isinstance(e, ast.Subscript):  # one item of a container
            return {_UNKNOWN if v is _EVERY else v for v in self.elements(e.value, ctx, seen)}
        if isinstance(e, ast.Attribute):
            return {_PYTHON} if self.qualname(e, ctx["path"]) == "sys.executable" else {_UNKNOWN}
        if isinstance(e, ast.IfExp):
            return _one_of(self.words(e.body, ctx, seen) | self.words(e.orelse, ctx, seen))
        if isinstance(e, ast.BoolOp):
            return _one_of(set().union(*(self.words(v, ctx, seen) for v in e.values)))
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.Div):
                return {_PATH}
            if isinstance(e.op, ast.Add):
                left = _one_of(self.words(e.left, ctx, seen))
                right = _one_of(self.words(e.right, ctx, seen))
                if isinstance(e.left, (ast.List, ast.Tuple)) or isinstance(e.right, (ast.List, ast.Tuple)):
                    return left | right
                if all(isinstance(v, str) for v in left | right) and len(left) * len(right) <= _MAX_ALTERNATIVES:
                    return {a + b for a in left for b in right}
            return {_UNKNOWN}
        if isinstance(e, ast.JoinedStr):
            return self._fstring_words(e, ctx, seen)
        if isinstance(e, ast.Call):
            name = ast.unparse(e.func)
            qualname = self.qualname(e.func, ctx["path"]) or name
            if name in ("list", "tuple", "sorted", "set", "frozenset", "reversed"):
                return set().union(set(), *(self.elements(a, ctx, seen) for a in e.args))
            if qualname == "shutil.which" and e.args:
                return self.words(e.args[0], ctx, seen)
            if qualname in _PATH_CALLS or name.endswith("Path") or qualname.startswith("os.path."):
                values = _one_of(set().union(set(), *(self.words(a, ctx, seen) for a in e.args[:1])))
                return values if values and all(isinstance(v, str) for v in values) else {_PATH}
            returns = self._returns(e, ctx)
            if returns is not None:
                return self._returned(returns, lambda r, c: self.words(r, c), self._substitute_words, set.union)
        return {_UNKNOWN}

    def _fstring_words(self, e, ctx, seen):
        texts = [""]
        for v in e.values:
            values = {str(v.value)} if isinstance(v, ast.Constant) else _one_of(self.words(v.value, ctx, seen))
            if not all(isinstance(x, str) for x in values) or len(texts) * len(values) > _MAX_ALTERNATIVES:
                head = str(e.values[0].value) if isinstance(e.values[0], ast.Constant) else ""
                # f"--root={path}" is an option with its value
                return {head} if head.startswith("--") and head.endswith("=") else {_UNKNOWN}
            texts = [a + b for a in texts for b in values]
        return set(texts)

    def _returned(self, returns, value_of, substitute, merge):
        out = None
        for key, exprs, callee_ctx, binding in returns:
            self._returning.add(key)
            try:
                for r in exprs:
                    part = substitute(value_of(r, callee_ctx), binding)
                    out = part if out is None else merge(out, part)
            finally:
                self._returning.discard(key)
        return out

    def elements(self, e, ctx, seen=frozenset()):
        """Values one item of the iterable *e* may take."""
        if self.is_registry(e, ctx["path"]):
            return {_EVERY}
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return self.words(e, ctx, seen)
        if isinstance(e, ast.Dict):
            return self.words(e, ctx, seen)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.elements(e.left, ctx, seen) | self.elements(e.right, ctx, seen)
        if isinstance(e, ast.Call):
            name = ast.unparse(e.func)
            if name in ("list", "tuple", "sorted", "set", "frozenset", "reversed") and e.args:
                return self.elements(e.args[0], ctx, seen)
            if isinstance(e.func, ast.Attribute) and e.func.attr in ("items", "keys", "values") and not e.args:
                return self.elements(e.func.value, ctx, seen)
            returns = self._returns(e, ctx)
            if returns is not None:
                return self._returned(returns, lambda r, c: self.elements(r, c), self._substitute_words, set.union)
            return {_UNKNOWN}
        if isinstance(e, ast.Name):
            return self._name_values(e.id, ctx, seen, self.elements, seq=True)
        return {_UNKNOWN}

    def _name_words(self, name, ctx, seen):
        return self._name_values(name, ctx, seen, self.words, seq=False)

    def _name_values(self, name, ctx, seen, value_of, seq):
        """Values of *name* as a word (``value_of=words``) or as an iterable's item (``elements``)."""
        if name in seen:
            return {_UNKNOWN}
        seen = seen | {name}
        if name in ctx["parametrized"]:
            out = set()
            cases = ctx["parametrized"][name]
            for case in cases:
                if isinstance(case, tuple):  # ("iter", expr): each item is a case
                    out |= self.elements(case[1], ctx, seen) if not seq else {_UNKNOWN}
                else:
                    out |= _one_of(value_of(case, ctx, seen))
            items = [c for c in cases if not isinstance(c, tuple)]
            if len(cases) == 1 and isinstance(cases[0], tuple):
                container = self._literal_container(cases[0][1], ctx)
                items = list(container.elts) if container is not None else None
            if not seq and items is not None and self._distinct_items(items, ctx, seen):
                out.add(_EACH)  # every case runs
            return out
        events = ctx["events"].get(name)
        if events:
            if not seq and len(events) == 1 and events[0][0] == "iter":
                container = self._literal_container(events[0][1], ctx)
                if container is not None and self._distinct_items(container.elts, ctx, seen):
                    values = _one_of(self.elements(container, ctx, seen))
                    exits, direct = self._loop_exit_state(events[0][1], ctx)
                    if exits:
                        values.add(_CONDITIONAL)
                        # Only a direct body statement before every possible exit
                        # establishes the first value; conditional sites may not run.
                        if direct and isinstance(container, (ast.List, ast.Tuple)) and container.elts:
                            first = self.words(container.elts[0], ctx, seen)
                            if len(first) == 1:
                                values |= {_Certain(v) for v in first if isinstance(v, str)}
                        return values
                    return values | {_EACH}  # every item runs
            out = set()
            for kind, value, _flag in events:
                if kind == "assign":
                    out |= value_of(value, ctx, seen)
                elif kind in ("iter", "unpack") and value_of == self.words:
                    out |= self.elements(value, ctx, seen)
                elif kind in ("append", "insert", "insert_any"):
                    out |= self.words(value, ctx, seen)
                elif kind == "extend":
                    out |= self.elements(value, ctx, seen)
                else:
                    out.add(_UNKNOWN)
            if name in ctx["params"]:
                out |= self._param(name, ctx, seq)
            return out if len(events) == 1 and name not in ctx["params"] else _one_of(out)
        if name in ctx["params"]:
            return self._param(name, ctx, seq)
        if self.is_registry(ast.Name(id=name), ctx["path"]):
            return {_EVERY} if seq else {_UNKNOWN}
        outer = None if name in ctx.get("globals", ()) else ctx.get("enclosing")
        while outer is not None:
            enclosing = self._context(outer, ctx["path"], _is_root(outer))
            if name in enclosing["events"] or name in enclosing["params"]:
                # A captured parameter is not the inner function's parameter.
                values = self._name_values(name, enclosing, seen - {name}, value_of, seq)
                return {_UNKNOWN if isinstance(v, _Ref) else v for v in values}
            outer = enclosing.get("enclosing")
        sources = self._global_sources(name, ctx)
        if sources is None:
            return {_UNKNOWN}
        return set().union(set(), *(value_of(v, c, seen) for v, c in sources))

    def _literal_container(self, e, ctx):
        """The list / tuple / set literal *e* is, or a name bound once to one, else None."""
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return e
        if not isinstance(e, ast.Name) or e.id in ctx["params"]:
            return None
        events = ctx["events"].get(e.id)
        if events is not None:
            sources = [value for kind, value, _flag in events if kind == "assign"] if len(events) == 1 else []
        else:
            sources = [v for v, _c in self._global_sources(e.id, ctx) or ()]
        if len(sources) == 1 and isinstance(sources[0], (ast.List, ast.Tuple, ast.Set)):
            return sources[0]
        return None

    def _distinct_items(self, items, ctx, seen):
        """Every item names at most one literal: iterating them runs each, not one of them."""
        for item in items:
            if isinstance(item, ast.Starred):
                return False
            strings = {v for v in self.words(item, ctx, seen) if isinstance(v, str)}
            if len(strings) > 1:
                return False
        return True

    def _global_sources(self, name, ctx):
        """Resolve enclosing assignments before module globals and imported names."""
        outer = None if name in ctx.get("globals", ()) else ctx.get("enclosing")
        while outer is not None:
            enclosing = self._context(outer, ctx["path"], _is_root(outer))
            if name in enclosing["events"] or name in enclosing["params"]:
                events = enclosing["events"].get(name, ())
                if name not in enclosing["params"] and all(k == "assign" for k, _v, _f in events):
                    return [(v, enclosing) for _k, v, _f in events]
                return None  # a binding with unresolved contents shadows the global
            outer = enclosing.get("enclosing")
        info = self.file(ctx["path"])
        if info and name in info["rebound_globals"]:
            return None  # a function rebinds it through ``global``
        if info and name in info["globals"]:
            return [(v, self._module_context(ctx["path"])) for v in info["globals"][name]]
        t = info["imports"].get(name) if info else None
        other = self.file(t[1][0]) if t and t[0] == "function" else None
        if other and t[1][1] in other["rebound_globals"]:
            return None
        if other and t[1][1] in other["globals"]:
            return [(v, self._module_context(t[1][0])) for v in other["globals"][t[1][1]]]
        return None

    # -- argv sequences -------------------------------------------------------

    def _is_text(self, e, ctx, depth=0):
        if isinstance(e, ast.Constant):
            return isinstance(e.value, str)
        if isinstance(e, ast.JoinedStr):
            return True
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self._is_text(e.left, ctx, depth) or self._is_text(e.right, ctx, depth)
        if isinstance(e, ast.Name) and depth < 4:
            events = ctx["events"].get(e.id)
            if events:
                return all(k == "assign" and self._is_text(v, ctx, depth + 1) for k, v, _f in events)
            sources = None if e.id in ctx["params"] else self._global_sources(e.id, ctx)
            return bool(sources) and all(self._is_text(v, c, depth + 1) for v, c in sources)
        return False

    def _string_parts(self, e, ctx, depth=0):
        """Alternatives of a string expression as literal text and interpolated value sets."""
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return [[e.value]]
        if isinstance(e, ast.JoinedStr):
            return [
                [str(v.value) if isinstance(v, ast.Constant) else frozenset(self.words(v.value, ctx)) for v in e.values]
            ]
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            left, right = self._string_parts(e.left, ctx, depth), self._string_parts(e.right, ctx, depth)
            return [a + b for a in left for b in right][:_MAX_ALTERNATIVES]
        if isinstance(e, ast.Name) and depth < 4:
            events = ctx["events"].get(e.id)
            if events and all(k == "assign" for k, _v, _f in events):
                return [p for _k, v, _f in events for p in self._string_parts(v, ctx, depth + 1)]
            sources = None if events or e.id in ctx["params"] else self._global_sources(e.id, ctx)
            if sources:
                return [p for v, c in sources for p in self._string_parts(v, c, depth + 1)]
        return [[frozenset(self.words(e, ctx))]]

    def _text_seqs(self, e, ctx):
        return [seq for parts in self._string_parts(e, ctx) for seq in _split_sequences(parts)]

    def _argv_seqs(self, exprs, ctx):
        """Argv alternatives of an in-process invocation (``CliRunner.invoke`` splits a string)."""
        if not exprs:
            return [()]
        if self._is_text(exprs[0], ctx):
            return self._text_seqs(exprs[0], ctx)
        return self.seqs(exprs[0], ctx)

    def _process_seqs(self, call, qualname, ctx):
        """Argv alternatives of a process start that runs no shell."""
        if qualname in _PROCESS_VARARGS:
            return self._seq_of(call.args, ctx)
        args = [*call.args[:1], *(k.value for k in call.keywords if k.arg in ("args", "cmd"))][:1]
        if not args:
            return []
        return self.seqs(args[0], ctx)

    def _seq_of(self, elts, ctx, seen=frozenset()):
        out = [()]
        for elt in elts:
            if isinstance(elt, ast.Starred):
                out = _concat(out, self.seqs(elt.value, ctx, seen))
            else:
                out = _concat(out, [(_slot(self.words(elt, ctx, seen)),)])
        return out

    def seqs(self, e, ctx, seen=frozenset()):
        """Alternatives of an argv expression, each a tuple of slots."""
        if isinstance(e, (ast.List, ast.Tuple)):
            return self._seq_of(e.elts, ctx, seen)
        if isinstance(e, ast.Name):
            return self._name_seqs(e.id, ctx, seen)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add) and not self._is_text(e, ctx):
            return _concat(self.seqs(e.left, ctx, seen), self.seqs(e.right, ctx, seen))
        if isinstance(e, ast.IfExp):  # one branch runs: neither is proven
            branches = [*self.seqs(e.body, ctx, seen), *self.seqs(e.orelse, ctx, seen)]
            return [tuple((v, True, s) for v, _o, s in alt) for alt in branches][:_MAX_ALTERNATIVES]
        if isinstance(e, ast.Call):
            name = ast.unparse(e.func)
            if name in ("list", "tuple") and len(e.args) == 1:
                return self.seqs(e.args[0], ctx, seen)
            if (self.qualname(e.func, ctx["path"]) or name) == "shlex.split" and e.args:
                return self._text_seqs(e.args[0], ctx)
            returns = self._returns(e, ctx)
            if returns is not None:
                out = self._returned(
                    returns,
                    lambda r, c: self.seqs(r, c),
                    lambda alts, b: [s for alt in alts for s in self._substitute(alt, b)],
                    lambda a, b: (a + b)[:_MAX_ALTERNATIVES],
                )
                if out:
                    return out
        if self._is_text(e, ctx):
            return self._text_seqs(e, ctx)
        return [(_slot(self.elements(e, ctx, seen), splice=True),)]

    def _element_seqs(self, e, ctx, seen):
        """Argv alternatives of one item of the iterable *e* (``for args in (...)``)."""
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            out = [s for elt in e.elts for s in self.seqs(elt, ctx, seen)]
            return out if len(out) <= _MAX_ALTERNATIVES else _concat(out, [()])
        if isinstance(e, ast.Name):
            events = ctx["events"].get(e.id)
            if events and all(k == "assign" for k, _v, _f in events):
                return [s for _k, v, _f in events for s in self._element_seqs(v, ctx, seen | {e.id})]
        return [(_slot({_UNKNOWN if v is _EVERY else v for v in self.elements(e, ctx, seen)}, splice=True),)]

    def _name_seqs(self, name, ctx, seen):
        unresolved = [(_slot({_UNKNOWN}, splice=True),)]
        if name in seen:
            return unresolved
        seen = seen | {name}
        if name in ctx["parametrized"]:
            out = []
            for case in ctx["parametrized"][name]:
                out.extend(
                    self._element_seqs(case[1], ctx, seen) if isinstance(case, tuple) else self.seqs(case, ctx, seen)
                )
            return out[:_MAX_ALTERNATIVES] if len(out) <= _MAX_ALTERNATIVES else _concat(out, [()])
        events = ctx["events"].get(name)
        if events:
            bases, mutations = [], []
            for kind, value, flag in events:
                if kind == "assign":
                    bases.append(self.seqs(value, ctx, seen))
                elif kind == "iter":
                    alternatives = self._element_seqs(value, ctx, seen)
                    exits, direct = self._loop_exit_state(value, ctx)
                    if exits:
                        # Preserve each possible argv without claiming it runs.
                        alternatives = [tuple((v, True, s) for v, _o, s in alt) for alt in alternatives]
                        container = self._literal_container(value, ctx)
                        if direct and isinstance(container, (ast.List, ast.Tuple)) and container.elts:
                            alternatives += self.seqs(container.elts[0], ctx, seen)
                    bases.append(alternatives)
                elif kind in ("unpack", "opaque"):
                    words = self.elements(value, ctx, seen) if value is not None else {_UNKNOWN}
                    bases.append([(_slot(words, splice=True),)])
                else:
                    mutations.append((kind, value, flag))
            if name in ctx["params"]:
                bases.append([(_slot(self._param(name, ctx, True), splice=True),)])
            if len(bases) > 1 and mutations or any(k == "insert_any" for k, _v, _f in mutations):
                # rebinding and mutation interleave: keep every word, not their order
                words = {v for alts in bases for seq in alts for vals, _o, _s in seq for v in vals}
                for kind, value, _flag in mutations:
                    words |= (
                        self.words(value, ctx, seen)
                        if kind.startswith(("append", "insert"))
                        else self.elements(value, ctx, seen)
                    )
                return [(_slot(words, splice=True),)]
            out = [alt for alts in bases for alt in alts] or [()]
            for kind, value, flag in mutations:
                if kind in ("append", "insert"):
                    part = [(_slot(self.words(value, ctx, seen), flag == "cond", flag == "loop"),)]
                elif flag == "loop":
                    part = [(_slot(self.elements(value, ctx, seen), splice=True),)]
                else:
                    part = self.seqs(value, ctx, seen)
                    if flag == "cond":
                        part = [tuple((v, True, s) for v, _o, s in alt) for alt in part]
                out = _concat(part, out) if kind == "insert" else _concat(out, part)
            return out
        if name in ctx["params"]:
            return [(_slot(self._param(name, ctx, True), splice=True),)]
        sources = None if self.is_registry(ast.Name(id=name), ctx["path"]) else self._global_sources(name, ctx)
        if not sources:
            return unresolved
        out = [alt for v, c in sources for alt in self.seqs(v, c, seen)]
        return out if len(out) <= _MAX_ALTERNATIVES else _concat(out, [()])


def _applying_conftests(path, conftests):
    """The conftests in *path*'s folder or a folder above it."""
    folder = path.replace("\\", "/").rpartition("/")[0]
    applying = []
    for c in conftests:
        where = c.replace("\\", "/").rpartition("/")[0]
        if c != path and (not where or folder == where or folder.startswith(where + "/")):
            applying.append(c)
    return applying


def _loaded_effects(scope, path, applying):
    """``(launches, writes_environment)`` for a test module, the conftests that apply and the code they load."""
    launches, writes = scope.loaded_effects(path, applying)
    return launches, writes or scope.writes_environment(path)


def _dispatch_relations(conn, root, registries, reached, handler_keys, exclude):
    """Test functions that may run a handler behind the *reached* registries.

    Returns ``(cli_hits, every_hits, possible_hits)``: ``{test_file: key}``
    for tests proven to run one of *handler_keys*; ``{(test_file,
    function): hops_from_registry}`` for tests that run every registry
    entry; ``{test_file: via}`` for tests the analysis cannot rule out,
    *via* being the key or ``"<registry>[*]"`` when any key may run.  A
    test that starts the package only in a subprocess imports none of it,
    so any process-starting test file is read too.
    """
    cli_hits, every_hits, possible_hits = {}, {}, {}
    if not reached:
        return cli_hits, every_hits, possible_hits
    flood_hops = _bfs_reverse_callers(conn, set(reached))
    flood = {}
    for row in batched_in(
        conn,
        "SELECT s.id, s.name, f.path FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
        list(flood_hops),
    ):
        flood[(row["path"], row["name"])] = flood_hops[row["id"]][0]
    module_paths, test_paths = {}, []
    for row in conn.execute("SELECT path FROM files WHERE language = 'python'").fetchall():
        module = _python_module_name(row["path"])
        if module:
            module_paths.setdefault(module, row["path"])
        if is_test_file(row["path"]) and row["path"] not in exclude:
            test_paths.append(row["path"])
    by_registry = {registries[rid][:2]: registries[rid][2] for rid in reached}
    registry_name = registries[reached[0]][1]
    scope = _DispatchScope(root, module_paths, flood, by_registry)
    conftests = [p for p in test_paths if p.replace("\\", "/").rsplit("/", 1)[-1] == "conftest.py"]
    # A direct production registry subscript may have no symbol edge to
    # the registry. Inspect all test roots so that graph incompleteness
    # cannot exclude a caller before readable() checks its dispatch body.
    for path in sorted(test_paths):
        info = scope.file(path)
        if info is None:
            continue
        applying = _applying_conftests(path, conftests)
        launches, writes = _loaded_effects(scope, path, applying)
        fixture_reach = scope.fixture_reach(path, applying)
        for fn_name in [*sorted(info["functions"]), None]:  # None: module-level code runs on import
            reach = scope.reach_root(path, fn_name)
            if fn_name is None:
                reach.update(fixture_reach)  # the conftest fixtures its tests run
            if launches:
                reach.any = True  # a loaded helper starts a process whose argv is not read
            if reach.unrelated and writes:
                reach.any = True  # the allowlisted program may be found anywhere or load any code
            if reach.every:
                name = fn_name or "<module>"
                every_hits[(path, name)] = flood.get((path, name), 1)
            hit = sorted(reach.run & handler_keys)
            if hit and path not in cli_hits:
                cli_hits[path] = hit[0]
            maybe = sorted(reach.may & handler_keys)
            if (maybe or reach.any) and path not in possible_hits:
                possible_hits[path] = maybe[0] if maybe else f"{registry_name}[*]"
        scope.release(path)
    return cli_hits, every_hits, possible_hits


# ---------------------------------------------------------------------------
# Core: gather affected tests for a set of symbol IDs
# ---------------------------------------------------------------------------


_KIND_ORDER = {"DIRECT": 0, "CLI_INVOKE": 1, "TRANSITIVE": 2, "MODULE_IMPORT": 3, "CLI_POSSIBLE": 4, "COLOCATED": 5}


def count_kinds(results):
    """Per-kind entry counts keyed by envelope field name (``"cli_invoke"``, ...).

    Every kind is present, so consumers' counts always sum to ``len(results)``.
    """
    counts = {kind.lower(): 0 for kind in _KIND_ORDER}
    for r in results:
        counts[r["kind"].lower()] = counts.get(r["kind"].lower(), 0) + 1
    return counts


def _gather_affected_tests(conn, target_sym_ids, target_file_paths, root=None):
    """Return a sorted list of affected test entries.

    Each entry is a dict with keys:
        file, symbol (optional),
        kind (DIRECT|CLI_INVOKE|TRANSITIVE|MODULE_IMPORT|CLI_POSSIBLE|COLOCATED),
        hops, via (optional).
    """
    root = Path(root) if root is not None else find_project_root()
    registries, handler_keys = _keyed_registries(conn, root)

    # BFS from all target symbols; keyed registries are recorded, not expanded
    reachable = _bfs_reverse_callers(conn, target_sym_ids, frozenset(registries))

    # Collect caller symbols that live in test files
    test_entries = {}  # keyed by (file, symbol_name) to dedupe

    if reachable:
        caller_ids = [sid for sid in reachable if sid not in target_sym_ids]
        if caller_ids:
            rows = batched_in(
                conn,
                "SELECT s.id, s.name, s.kind, f.path as file_path "
                "FROM symbols s "
                "JOIN files f ON s.file_id = f.id "
                "WHERE s.id IN ({ph})",
                caller_ids,
            )

            for r in rows:
                if not is_test_file(r["file_path"]):
                    continue
                hops, via = reachable[r["id"]]
                key = (r["file_path"], r["name"])
                kind = "DIRECT" if hops == 1 else "TRANSITIVE"

                # Keep the shortest path if we see a duplicate
                if key in test_entries and test_entries[key]["hops"] <= hops:
                    continue

                test_entries[key] = {
                    "file": r["file_path"],
                    "symbol": r["name"],
                    "symbol_kind": r["kind"],
                    "kind": kind,
                    "hops": hops,
                    "via": via if hops > 1 else None,
                }

    # File-level relations with no call edge into the target.  A file is
    # listed once: only files not already selected gain a file-level entry.
    seen_files = {e["file"] for e in test_entries.values()}

    def _add_file_entry(path, kind, via):
        if path in seen_files:
            return
        seen_files.add(path)
        test_entries[(path, None)] = {
            "file": path,
            "symbol": None,
            "symbol_kind": None,
            "kind": kind,
            "hops": None,
            "via": via,
        }

    # Through a reached keyed registry: a test proven to run a reached
    # handler's key is CLI_INVOKE; one that runs every entry keeps the path
    # (TRANSITIVE); one the analysis cannot rule out is CLI_POSSIBLE.
    reached = sorted((reachable[rid][0], rid) for rid in registries if rid in reachable)
    target_keys = set()
    for sid in reachable:
        target_keys.update(handler_keys.get(sid, ()))
    exclude = set(target_file_paths)
    cli_hits, every_hits, possible_hits = _dispatch_relations(
        conn, root, registries, [rid for _h, rid in reached], target_keys, exclude
    )
    if every_hits:
        registry_hops, registry_id = reached[0]
        kinds = {}
        for r in batched_in(
            conn,
            "SELECT s.name, s.kind, f.path FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.path IN ({ph})",
            sorted({path for path, _name in every_hits}),
        ):
            kinds[(r["path"], r["name"])] = r["kind"]
        for (path, name), hops in sorted(every_hits.items()):
            hops += registry_hops
            key = (path, name)
            if key in test_entries and test_entries[key]["hops"] <= hops:
                continue
            test_entries[key] = {
                "file": path,
                "symbol": name,
                "symbol_kind": kinds.get(key, "function"),
                "kind": "TRANSITIVE",
                "hops": hops,
                "via": registries[registry_id][1],
            }
            seen_files.add(path)

    # MODULE_IMPORT: the test imports a target module.
    target_modules = {m for m in map(_python_module_name, target_file_paths) if m}
    import_hits = _test_module_imports(conn, root, target_modules, exclude)
    for path, key in cli_hits.items():
        _add_file_entry(path, "CLI_INVOKE", key)
    for path, module in import_hits.items():
        _add_file_entry(path, "MODULE_IMPORT", module)
    for path, via in sorted(possible_hits.items()):
        _add_file_entry(path, "CLI_POSSIBLE", via)

    # Colocated tests (filename-pattern match, not in call graph)
    for cf in _find_colocated_tests(conn, set(target_file_paths)):
        _add_file_entry(cf, "COLOCATED", None)

    # Sort: DIRECT, CLI_INVOKE, TRANSITIVE by hop count, MODULE_IMPORT, CLI_POSSIBLE, COLOCATED
    results = sorted(
        test_entries.values(),
        key=lambda e: (_KIND_ORDER.get(e["kind"], 9), e["hops"] or 999, e["file"]),
    )

    return results


# ---------------------------------------------------------------------------
# Resolve targets -> (symbol_ids, file_paths)
# ---------------------------------------------------------------------------


def _resolve_file_symbols(conn, path):
    """Return ``(sym_ids, fpaths, tier)`` for a file-path-like target.

    Pattern-1 Variant D Wave B shim (audit reference:
    ``(internal memo)``). Delegates to
    :func:`roam.commands.resolve.resolve_file_symbols` so the silent
    LIKE %name substring fallback is surfaced via a tier discriminator
    rather than collapsed into the same shape as an exact-path match.

    Returns a 3-tuple ``(sym_ids, fpaths, tier)``:

    - ``sym_ids``: ``set[int]`` of symbol ids owned by the resolved file
      (empty on miss).
    - ``fpaths``: ``set[str]`` containing the canonical file path
      (empty on miss).
    - ``tier``: ``"file"`` (exact match), ``"file_substring"`` (LIKE
      fallback), or ``None`` (no match). Pass directly to
      :func:`roam.output.formatter.resolution_disclosure` so callers
      flip ``partial_success: true`` on the substring path.

    Pre-Wave-B this helper returned a 2-tuple ``(sym_ids, fpaths)`` and
    silently collapsed both tiers — the canonical Variant D failure
    shape. ``cmd_preflight`` and ``cmd_plan`` import this name; both
    are updated to consume the new 3-tuple shape in the same wave.
    """
    _file_id, sym_ids, file_path, tier = resolve_file_symbols(conn, path)
    if file_path is None:
        return set(), set(), None
    return sym_ids, {file_path}, tier


def _looks_like_file(target):
    """Heuristic: does the target string look like a file path?"""
    return "/" in target or "\\" in target or target.endswith(".py")


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    name="affected-tests",
    category="workflow",
    summary="Trace from a changed symbol or file to test files that exercise it",
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
@click.command("affected-tests")
@click.argument("target", required=False, default=None)
@click.option("--staged", is_flag=True, help="Find tests for staged changes")
@click.option(
    "--command",
    "show_command",
    is_flag=True,
    help="Output a runnable pytest command",
)
@click.pass_context
def affected_tests_cmd(ctx, target, staged, show_command):
    """Trace from a changed symbol or file to test files that exercise it.

    Unlike ``test-map`` (which maps test topology for a specific symbol),
    this command finds all tests affected by staged or specified changes.

    TARGET is a symbol name or file path.  Use --staged to automatically
    find tests for all staged changes.

    \b
    Kinds, strongest first:
      DIRECT         a test symbol calls or references the target
      CLI_INVOKE     a test runs the target's registered key ("pr-replay")
                     in command position of an argv: in process, e.g.
                     CliRunner().invoke(cli, [...]), or in a subprocess
                     (python -m roam pr-replay, roam pr-replay)
      TRANSITIVE     a test reaches the target through callers; at a keyed
                     registry the walk continues only into tests that run
                     every entry of the registry
      MODULE_IMPORT  a test imports the target's module
      CLI_POSSIBLE   the analysis cannot rule out that a test runs the
                     target: a computed or read-only key, the key after
                     the command, an unresolved argv or program
      COLOCATED      a test file matches by directory or naming convention
    CLI_INVOKE, MODULE_IMPORT, CLI_POSSIBLE and COLOCATED are file-level:
    they list only test files that no stronger entry already selects.

    \b
    Code a test loads is read too: the conftests that apply, modules in
    pytest_plugins, and project modules imported up to 3 imports deep.
    A conftest fixture a test requests by name, or an autouse one, counts
    as part of the test; an import under if TYPE_CHECKING: proves nothing.
    Blind spots, named rather than chased: dynamic imports (importlib,
    __import__ with a computed name), setters and launchers fetched through
    getattr (getattr(subprocess, 'run')), argv changed at run time before
    the launch by setup_module or other module-level mutation, fixtures
    requested dynamically through request.getfixturevalue, plugins
    registered by entry points or the -p flag, launches from production
    code the test calls, and anything past 3 imports.

    \b
    Examples:
      roam affected-tests handle_login
      roam affected-tests src/auth.py
      roam affected-tests --staged --show-command

    See also ``test-map`` (test topology for one symbol), ``preflight``
    (pre-change safety), and ``pr-risk`` (overall PR risk score).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    if not target and not staged:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "affected-tests",
                        summary={
                            "verdict": "no TARGET symbol/file or --staged provided",
                            "state": "usage_error",
                            "partial_success": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="no TARGET symbol/file or --staged provided",
                        hint="Pass a TARGET symbol/file or use --staged.",
                    )
                )
            )
        else:
            click.echo("Provide a TARGET symbol/file or use --staged.")
        raise SystemExit(1)

    with open_db(readonly=True) as conn:
        all_sym_ids = set()
        all_file_paths = set()
        target_label = target or "staged changes"
        # W1245 Pattern-2 variant-D resolution-state tracking. The symbol-
        # target branch below walks ``find_symbol``'s 3-tier resolver chain
        # (qualified -> simple -> LIKE-fallback); the disclosure flips to
        # ``fuzzy`` whenever the input string didn't exact-match a single
        # symbol. The --staged and file-target branches don't go through
        # ``find_symbol`` so they stay implicit ``symbol`` (multi-target
        # set-builds aren't degraded; ``staged`` mode is a Pattern-2c
        # legitimate aggregate rather than a fallback).
        resolution_tier: str = "symbol"
        resolved_target_label: str | None = None

        # --staged mode: resolve changed files to symbols
        if staged:
            root = find_project_root()
            changed = get_changed_files(root, staged=True)
            if not changed:
                click.echo("No staged changes found.")
                return
            file_map = resolve_changed_to_db(conn, changed)
            if not file_map:
                click.echo("Staged files not found in index. Try `roam index` first.")
                return
            for path, fid in file_map.items():
                all_file_paths.add(path)
                syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
                all_sym_ids.update(s["id"] for s in syms)
            target_label = f"staged changes ({len(file_map)} files)"

        # Explicit target (may combine with --staged)
        if target:
            target_norm = target.replace("\\", "/")
            if _looks_like_file(target_norm):
                sym_ids, fpaths, file_tier = _resolve_file_symbols(conn, target_norm)
                # Pattern-1 Variant D Wave B: ``file_tier`` distinguishes
                # an exact-path resolution (``"file"``) from a degraded
                # LIKE %name substring fallback (``"file_substring"``).
                # An empty sym_ids set on a successful ``file`` resolution
                # is a valid shape (file indexed with zero symbols), so
                # gate the not-found branch on tier=None rather than
                # ``not sym_ids`` — preserves the resolved-but-empty
                # tier-disclosable shape.
                if file_tier is None:
                    click.echo(f"File not found in index: {target}")
                    raise SystemExit(1)
                resolution_tier = file_tier
                all_sym_ids.update(sym_ids)
                all_file_paths.update(fpaths)
                # ``resolved_target_label`` echoes the canonical path that
                # was actually resolved (post-substring-match). Surfaces
                # the substring drift in the disclosure.target field so
                # agents see the gap between input and resolved file.
                if fpaths:
                    resolved_target_label = next(iter(fpaths))
            else:
                sym = find_symbol(conn, target)
                if sym is None:
                    # W1245 Pattern-2 variant-D: structured unresolved
                    # envelope on JSON mode so MCP consumers see the
                    # same disclosure shape as the resolved branches.
                    if json_mode:
                        unresolved_disclosure = resolution_disclosure("unresolved", target=target)
                        click.echo(
                            to_json(
                                json_envelope(
                                    "affected-tests",
                                    summary={
                                        "verdict": f"Symbol not found: {target}",
                                        "partial_success": True,
                                        "state": "not_found",
                                        **unresolved_disclosure,
                                    },
                                    **unresolved_disclosure,
                                )
                            )
                        )
                        raise SystemExit(1)
                    click.echo(f"Symbol not found: {target}")
                    raise SystemExit(1)
                # W1245 \ W1249 Pattern-2 variant-D: ``find_symbol`` stamps
                # ``_resolution_tier`` on the returned row so the envelope
                # can distinguish a fully-resolved success from a degraded
                # fuzzy-match success that may have landed on a different
                # target.
                resolution_tier = sym.get("_resolution_tier", "symbol")
                resolved_target_label = sym["qualified_name"] or sym["name"]
                all_sym_ids.add(sym["id"])
                all_file_paths.add(sym["file_path"])
                target_label = f"{sym['name']} ({abbrev_kind(sym['kind'])}, {loc(sym['file_path'], sym['line_start'])})"

        # Gather affected tests
        results = _gather_affected_tests(conn, all_sym_ids, all_file_paths)

        # Unique test files for the pytest command
        seen_order = []
        seen_set = set()
        for r in results:
            if r["file"] not in seen_set:
                seen_set.add(r["file"])
                seen_order.append(r["file"])

        pytest_cmd = "pytest " + " ".join(seen_order) if seen_order else ""

        # --command mode: just print the command
        if show_command:
            if pytest_cmd:
                click.echo(pytest_cmd)
            else:
                click.echo("# No affected tests found.")
            return

        # SARIF output (W1160): projection for CI / GitHub Code Scanning.
        # Branches BEFORE json/text so the pre-existing paths stay
        # byte-identical to pre-W1160.
        if sarif_mode:
            from roam.output.sarif import affected_tests_to_sarif, write_sarif

            click.echo(
                write_sarif(
                    affected_tests_to_sarif(
                        {
                            "command": "affected-tests",
                            "summary": {"target": target_label},
                            "tests": [
                                {
                                    "file": r["file"],
                                    "symbol": r["symbol"],
                                    "kind": r["kind"],
                                    "hops": r["hops"],
                                    "via": r["via"],
                                }
                                for r in results
                            ],
                        }
                    )
                )
            )
            return

        # JSON output
        if json_mode:
            kind_counts = count_kinds(results)

            if results:
                verdict = f"{len(results)} tests affected ({len(seen_order)} files) for {target_label}"
            else:
                verdict = f"no tests affected for {target_label}"

            # W1245 Pattern-2 variant-D: suffix the verdict when the symbol
            # target resolved through a degraded tier so LAW-6 single-line
            # consumers still see the disclosure. The --staged + file paths
            # stay implicit ``symbol`` (set-build aggregations are not
            # fallback resolutions). Pattern-1 Variant D Wave B adds the
            # ``file_substring`` case: distinct from the exact-``file``
            # tier so agents can tell a substring LIKE-fallback match from
            # a fully-resolved exact-path success.
            if resolution_tier == "fuzzy":
                verdict = f"{verdict} [fuzzy resolution]"
            elif resolution_tier == "file_substring":
                verdict = f"{verdict} [file substring match]"
            disclosure = resolution_disclosure(
                resolution_tier,
                target=resolved_target_label if resolved_target_label is not None else target_label,
            )

            click.echo(
                to_json(
                    json_envelope(
                        "affected-tests",
                        summary={
                            "verdict": verdict,
                            "target": target_label,
                            "total_tests": len(results),
                            "direct": kind_counts["direct"],
                            "transitive": kind_counts["transitive"],
                            "colocated": kind_counts["colocated"],
                            "cli_invoke": kind_counts["cli_invoke"],
                            "module_import": kind_counts["module_import"],
                            "cli_possible": kind_counts["cli_possible"],
                            "test_files": len(seen_order),
                            **disclosure,
                        },
                        budget=token_budget,
                        tests=[
                            {
                                "file": r["file"],
                                "symbol": r["symbol"],
                                "kind": r["kind"],
                                "hops": r["hops"],
                                "via": r["via"],
                            }
                            for r in results
                        ],
                        pytest_command=pytest_cmd,
                        test_files=seen_order,
                        **disclosure,
                    )
                )
            )
            return

        # Text output
        if not results:
            click.echo(f"VERDICT: no tests affected for {target_label}.")
            return

        verdict = f"{len(results)} tests affected ({len(seen_order)} files) for {target_label}"
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Affected tests for {target_label}:\n")

        for r in results:
            kind_tag = f"{r['kind']:<13s}"

            if r["symbol"]:
                label = f"{r['file']}::{r['symbol']}"
            else:
                label = r["file"]

            if r["kind"] == "DIRECT":
                detail = f"({r['hops']} hop)"
            elif r["kind"] == "TRANSITIVE":
                via_str = f" via {r['via']}" if r["via"] else ""
                detail = f"({r['hops']} hops{via_str})"
            elif r["kind"] == "CLI_INVOKE":
                detail = f"(invokes '{r['via']}')"
            elif r["kind"] == "MODULE_IMPORT":
                detail = f"(imports {r['via']})"
            elif r["kind"] == "CLI_POSSIBLE":
                detail = f"(may invoke '{r['via']}')"
            else:
                detail = "(same directory)"

            click.echo(f"  {kind_tag} {label:<55s} {detail}")

        click.echo()
        if pytest_cmd:
            click.echo(f"Run: {pytest_cmd}")
