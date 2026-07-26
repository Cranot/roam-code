"""Guard: tests must not invoke roam's console script by bare name.

``subprocess.run(["roam", ...])`` resolves argv[0] through PATH. That is not
the interpreter running the tests, and on CI it is not roam at all: the
workflow installs into ``.venv`` and runs ``.venv/bin/python -m pytest``
without exporting ``.venv/bin``, so the console script is present on disk but
unreachable. The failure surfaces as

    FileNotFoundError: [Errno 2] No such file or directory: 'roam'

Locally it is worse than a hard failure -- it silently *succeeds* against
whatever ``roam`` happens to be on PATH, typically a stale global install from
a previous release. The test then exercises code that is not the code under
test, and agrees with itself right up until CI disagrees.

The house idiom, used by ``tests/conftest.py`` and most of the suite, is
``[sys.executable, "-m", "roam", ...]``. It binds the child process to the
interpreter running the tests, so it cannot miss and cannot drift to a stale
build.

This guard is deliberately a static check rather than a runtime one: the bug
only manifests where PATH lacks the venv, which is exactly the environment the
authoring developer is not in. A dynamic test would pass on the machine where
the mistake is made.

Generalises to any project shipping console-script entry points whose tests
shell out to them: assert the tests invoke the entry point through the running
interpreter, never through PATH lookup.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

# Names that must never appear as argv[0] in a test subprocess call.
_CONSOLE_SCRIPTS = {"roam"}

# String literals that merely *contain* the script name are fine -- hook bodies
# and generated shell snippets legitimately embed ``["roam", ...]`` as text to
# be asserted on. Only real call sites are checked, so this file lists the
# subprocess entry points whose first positional argument is the argv list.
_SUBPROCESS_CALLS = {"run", "check_output", "check_call", "call", "Popen"}


def _argv_list_head(node: ast.Call) -> ast.expr | None:
    """Return the first element of the argv list literal, if argv is a list."""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.List) and first.elts:
        return first.elts[0]
    return None


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_CALLS:
        # subprocess.run(...) / sp.Popen(...) / self.subprocess.run(...)
        return True
    return False


def _offending_calls(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        head = _argv_list_head(node)
        if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value in _CONSOLE_SCRIPTS:
            hits.append((node.lineno, head.value))
    return hits


def test_tests_invoke_roam_through_the_running_interpreter() -> None:
    """No test may shell out to the bare ``roam`` console script."""
    root = repo_root()
    offenders: list[str] = []

    for path in sorted((root / "tests").rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for lineno, script in _offending_calls(path):
            rel = path.relative_to(root).as_posix()
            offenders.append(f"{rel}:{lineno} invokes bare {script!r} via subprocess")

    assert not offenders, (
        "Tests must invoke roam as [sys.executable, '-m', 'roam', ...], not by bare "
        "console-script name -- PATH lookup misses the venv on CI and silently hits a "
        "stale global install locally:\n  " + "\n  ".join(offenders)
    )
