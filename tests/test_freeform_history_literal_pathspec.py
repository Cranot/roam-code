"""Every ``git log -- <target>`` in the compiler must force literal pathspecs.

The freeform-augment probe (``_probe_freeform_augment_for_task`` in
``src/roam/plan/compiler.py``) embeds ``git log -5`` for the task-named file.
The path reaches ``git log ... -- <target>`` as a normalized repo path that
may carry leading pathspec magic (``:(top)``, ``:(glob)``, ``:./...``) or
glob chars. ``--`` stops OPTION parsing but does NOT force literal pathspec
interpretation, so without ``GIT_LITERAL_PATHSPECS=1`` the magic broadens or
re-roots the matched commit set.

This guard AST-scans the module so any current or future ``git log`` call that
passes ``--`` (i.e. takes a pathspec target) is required to wire
``env=_git_literal_pathspec_env()``. It mirrors the behavioural coverage in
``test_git_cochange_literal_pathspec.py`` but pins the whole family, not one site.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tests._helpers.repo_root import repo_root


def _git_log_pathspec_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every subprocess.run(...) Call whose first arg is a list literal
    starting with 'git', containing 'log', and containing a bare '--' (the
    pathspec separator) — i.e. calls that pass a pathspec target to git log."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List):
            continue
        literals = [e.value for e in first.elts if isinstance(e, ast.Constant)]
        if not literals or literals[0] != "git" or "log" not in literals:
            continue
        if "--" not in literals:
            continue
        calls.append(node)
    return calls


def _has_literal_pathspec_env(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "env" and isinstance(kw.value, ast.Call):
            fn = kw.value.func
            if isinstance(fn, ast.Name) and fn.id == "_git_literal_pathspec_env":
                return True
    return False


def test_all_git_log_pathspec_calls_use_literal_env():
    src = (repo_root() / "src" / "roam" / "plan" / "compiler.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = _git_log_pathspec_calls(tree)
    # There is at least the freeform-augment history call + the cochange call.
    assert len(calls) >= 2, f"expected to find the git-log pathspec calls; found {len(calls)}"
    unguarded = [c.lineno for c in calls if not _has_literal_pathspec_env(c)]
    assert not unguarded, (
        f"git log -- <target> calls at lines {unguarded} pass a pathspec "
        f"target without env=_git_literal_pathspec_env(); leading magic such "
        f"as ':(top)' / ':(glob)' would re-root or broaden the commit set"
    )
