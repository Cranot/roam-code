"""A child interpreter must be launched UNRESOLVED, or the virtualenv is lost.

WHAT HAPPENED

``roam service-report`` and ``roam reachability-triage`` fan out to component
subprocesses. The launcher built the child argv as::

    argv = [os.path.realpath(sys.executable), "-m", "roam", ...]

CPython locates a virtualenv from the interpreter's own path -- it reads
``pyvenv.cfg`` from the directory beside it. On Linux and macOS, both
``python -m venv`` and ``uv venv`` create ``.venv/bin/python`` as a SYMLINK to
the base interpreter, so ``realpath`` resolved the child out of the environment
that has roam installed and into one that does not.

Every component then failed to import roam, printed nothing, and came back as
``component_empty_output``. ``service-report`` still exited 0 and still printed
all five of its sections -- a full-looking report assembled from six dead
components, which is this codebase's signature defect wearing its own uniform.

WHY NOTHING CAUGHT IT

Windows ``.venv\\Scripts\\python.exe`` is a real file, so ``realpath`` is a
no-op and the venv survives. Every local run on the development host passed.
The variable was never "Linux" -- it was "symlinked interpreter", and the two
happen to correlate.

It surfaced only as a CI/local disagreement in an unrelated authorization
guard: on Linux the components died, ``scan_incomplete`` went true, and
``reachability-triage --gate-on-new-reachable`` exited 5 instead of 0. The
guard reported that the command "no longer authorizes -- good", which read like
a fix landing. It was a launcher failing closed.

WHAT THIS TEST PINS

That no module re-invokes the running interpreter through ``realpath``.
``sys.executable`` is the canonical way to say "the interpreter I am running
under"; resolving it discards exactly the information that makes it useful.
"""

from __future__ import annotations

import ast

from tests._helpers.repo_root import repo_root

# W572/W588: ask git for the canonical toplevel rather than walking up from
# ``__file__``. A nested worktree has ``tests/`` but not the project markers, so
# the parents[] walk lands somewhere that exists and is wrong -- which for THIS
# guard would mean scanning an empty or partial tree and passing. A scanner that
# certifies what it never read is the defect the file below is about; it would
# be a poor place to commit it.
SRC = repo_root() / "src" / "roam"

# Module aliases in play: this codebase imports `os as _os` / `sys as _sys` in
# several command modules, so match on the ATTRIBUTE SHAPE rather than a name.
_RESOLVERS = {"realpath", "readlink"}


def _is_sys_executable(node: ast.AST) -> bool:
    """True for `sys.executable` / `_sys.executable`, however the module is bound."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id.lstrip("_") == "sys"
    )


def _resolver_name(func: ast.AST) -> str | None:
    """Return `realpath`/`readlink` if `func` is os.path.realpath-shaped."""
    if isinstance(func, ast.Attribute) and func.attr in _RESOLVERS:
        return func.attr
    return None


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolver = _resolver_name(node.func)
        if resolver is None:
            continue
        if any(_is_sys_executable(arg) for arg in node.args):
            found.append((node.lineno, resolver))
    return found


def test_detector_actually_detects() -> None:
    """Positive control: a guard that cannot fail is not a guard.

    Pinned because the real assertion below passes trivially if `_violations`
    silently matches nothing -- the same shape of vacuous pass this file exists
    to document.
    """
    caught = _violations(ast.parse("argv = [_os.path.realpath(_sys.executable), '-m', 'roam']"))
    assert caught == [(1, "realpath")], caught

    clean = _violations(ast.parse("argv = [_sys.executable, '-m', 'roam']"))
    assert clean == [], clean


def test_no_module_resolves_the_running_interpreter() -> None:
    offenders: list[str] = []
    scanned = 0
    unparseable: list[str] = []

    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # An unreadable file is UNKNOWN, not clean. Name it rather than
            # letting it drop out of the denominator.
            unparseable.append(f"{path.relative_to(repo_root())}: {type(exc).__name__}")
            continue
        scanned += 1
        for lineno, resolver in _violations(tree):
            offenders.append(f"{path.relative_to(repo_root())}:{lineno}  os.path.{resolver}(sys.executable)")

    assert not unparseable, "files this guard could not read (so cannot certify):\n  " + "\n  ".join(unparseable)
    assert scanned > 0, "scanned no files -- the guard is pointed at the wrong directory"
    assert not offenders, (
        "A child interpreter is being launched through a path resolver, which defeats\n"
        "virtualenv detection on every platform where `.venv/bin/python` is a symlink\n"
        "(Linux and macOS: both `python -m venv` and `uv venv`). Pass `sys.executable`\n"
        "unresolved.\n\n" + "\n".join(offenders)
    )
