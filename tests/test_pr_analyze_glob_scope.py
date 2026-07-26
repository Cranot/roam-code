"""``**`` in a rule's ``source_glob`` must include the base level.

THE DEFECT THIS GUARDS (fixed 2026-07-27): the matcher used ``fnmatch``, which
does not treat ``**`` specially -- it collapses to ``*``, and ``*`` cannot cross
``/``. So ``**/*.go`` compiled to "one segment, a slash, then *.go" and silently
failed to match ``main.go``; ``src/**/*.py`` failed to match ``src/main.py``.

That is a silent SCOPE HOLE. A rule reads as covering a whole subtree while
exempting everything at the top of it -- exactly where entry points and
top-level modules live. 124 of the 127 rules across the seven shipped language
packs use ``**``, so every pack had a hole at its own base.

It surfaced through a BLOCK-severity ``py-no-eval`` rule that never fired on an
``eval()`` added by a diff: shipped security tooling that could not block.
"""

from __future__ import annotations

import pytest

from roam.commands.pr_analyze.rules import path_matches_glob


@pytest.mark.parametrize(
    ("path", "glob"),
    [
        # The regression: base-level files under a ``**`` glob.
        ("main.go", "**/*.go"),
        ("src/main.py", "src/**/*.py"),
        ("internal/handlers/x.go", "internal/handlers/**/*.go"),
        # Nested files must keep matching.
        ("a/b/main.go", "**/*.go"),
        ("src/a/b/deep.py", "src/**/*.py"),
        # Plain globs unaffected.
        ("src/main.py", "src/*.py"),
        ("anything.py", "*.py"),
    ],
)
def test_glob_matches(path: str, glob: str) -> None:
    assert path_matches_glob(path, glob), f"{path!r} should match {glob!r}"


@pytest.mark.parametrize(
    ("path", "glob"),
    [
        # Scope must stay bounded -- a fix that matches everything is worse than
        # the hole it replaces.
        ("other/x.go", "internal/handlers/**/*.go"),
        ("src/main.js", "src/**/*.py"),
        ("tests/t.py", "src/**/*.py"),
        # NOTE: ("src/a/main.py", "src/*.py") is deliberately NOT here. `*`
        # crosses `/` in fnmatch, and every shipped rule was written against
        # that. Narrowing it would silently shrink 127 rules to fix a hole in
        # `**` -- a bigger scope change than the one being repaired.
        ("docs/readme.md", "**/*.go"),
    ],
)
def test_glob_does_not_overmatch(path: str, glob: str) -> None:
    assert not path_matches_glob(path, glob), f"{path!r} must NOT match {glob!r}"


def test_windows_separators_normalise() -> None:
    """Diffs on Windows must behave identically to POSIX ones."""
    assert path_matches_glob(r"src\main.py", "src/**/*.py")
