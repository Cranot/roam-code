"""Guard: the internal/dogfood skip hook must stay marker-based.

W-audit (2026-07-27): tests/conftest.py's ``pytest_collection_modifyitems``
used to skip a test whenever its *source file's text* contained the
substring ``internal/dogfood`` anywhere -- docstring, comment, or a mocked
argument value. That check was whole-file and substring-based, so a single
test in test_mcp_W306_wrappers.py that merely passed the string
``"internal/dogfood/evals/"`` to a *mocked* function (no real filesystem
access) took all 95 collected items in that file down with it.

Measured before the fix: 161 collected tests across 9 files were skipped on
any run without the corpus (i.e. on public CI, for every outside
contributor). Only 7 of those had a genuine dependency on the corpus; the
other 154 -- including a leak-gate correctness test
(test_leak_gate_exemplars.py) and a shipped-bug regression guard
(test_describe_stack_leak.py) -- never ran on the only environment where a
stranger's change gets checked.

This module pins the fix two ways:
  1. Structural: the hook implementation contains no source-text scan and
     is driven by the ``needs_dogfood`` marker via pytest's marker API.
  2. Behavioural: the hook actually skips a marked item and leaves an
     unmarked item alone when the corpus is reported absent.
"""

from __future__ import annotations

import ast
import pathlib

from tests._helpers.repo_root import repo_root

CONFTEST_PATH = repo_root() / "tests" / "conftest.py"


def _hook_function_node() -> ast.FunctionDef:
    tree = ast.parse(CONFTEST_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_collection_modifyitems":
            return node
    raise AssertionError(
        f"{CONFTEST_PATH} no longer defines pytest_collection_modifyitems -- "
        "the dogfood skip hook may have been removed or renamed. If the "
        "corpus-absence skip moved elsewhere, update this guard to point "
        "at the new location instead of deleting it."
    )


def test_needs_dogfood_marker_is_registered() -> None:
    """pyproject.toml must register the ``needs_dogfood`` marker.

    Without registration pytest emits PytestUnknownMarkWarning on every
    use, and -- more importantly -- an unregistered marker is a sign
    nobody has adopted it as the canonical mechanism.
    """
    pyproject = repo_root() / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "needs_dogfood:" in text, (
        f"{pyproject} does not register the 'needs_dogfood' marker under "
        "[tool.pytest.ini_options] markers. Add an entry so the marker is "
        "documented and the dogfood skip hook has something to key off."
    )


def test_skip_hook_has_no_source_text_scan() -> None:
    """The hook body must not read a test file's source text to decide skips.

    This is the exact regression: the prior hook called
    ``path.read_text(...)`` on each item's source file and checked for the
    substring "internal/dogfood". Re-pin against ANY ``read_text`` call
    inside the hook -- there is no legitimate reason for a marker-based
    skip decision to open test source files.
    """
    node = _hook_function_node()
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    read_text_calls = [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "read_text"]
    assert not read_text_calls, (
        "pytest_collection_modifyitems calls .read_text() -- this is the "
        "whole-file/substring-scan regression. The hook must decide skips "
        "from a pytest marker (item.get_closest_marker(...)), never from "
        "a test file's source text."
    )


def test_skip_hook_checks_needs_dogfood_marker() -> None:
    """The hook must consult the ``needs_dogfood`` marker via the pytest API."""
    full_text = CONFTEST_PATH.read_text(encoding="utf-8")
    node = _hook_function_node()
    source = ast.get_source_segment(full_text, node) or ""
    assert "needs_dogfood" in source, (
        "pytest_collection_modifyitems no longer mentions 'needs_dogfood' -- "
        "the hook must gate the skip on this marker, not on file contents."
    )
    assert "get_closest_marker" in source or "iter_markers" in source, (
        "pytest_collection_modifyitems does not call get_closest_marker()/"
        "iter_markers() -- the hook must ask pytest's marker API which "
        "items opted in, not infer it from file contents."
    )


def test_skip_hook_skips_marked_item_and_spares_unmarked_when_corpus_absent(monkeypatch) -> None:
    """Behavioural pin: with the corpus reported absent, a marked item is
    skipped and an unmarked item is left completely alone.

    This is the property that a substring-scan hook cannot have: two items
    from the SAME module, one with the marker and one without, must diverge.
    """
    from tests.conftest import pytest_collection_modifyitems

    real_is_dir = pathlib.Path.is_dir

    def fake_is_dir(self):
        # Report ONLY the internal/dogfood directory as absent; defer to
        # the real filesystem for everything else so we don't destabilize
        # unrelated collection/fixture machinery running in this process.
        if self.name == "dogfood" and self.parent.name == "internal":
            return False
        return real_is_dir(self)

    monkeypatch.setattr(pathlib.Path, "is_dir", fake_is_dir)

    class _FakeItem:
        def __init__(self, marked: bool):
            self._marked = marked
            self.added_markers: list = []

        def get_closest_marker(self, name):
            return object() if self._marked and name == "needs_dogfood" else None

        def add_marker(self, marker):
            self.added_markers.append(marker)

    marked_item = _FakeItem(marked=True)
    unmarked_item = _FakeItem(marked=False)

    pytest_collection_modifyitems(config=None, items=[marked_item, unmarked_item])

    assert marked_item.added_markers, "an item marked needs_dogfood must be skipped when the corpus is absent"
    assert not unmarked_item.added_markers, (
        "an item WITHOUT the needs_dogfood marker must never be skipped by this hook, "
        "regardless of what its source file contains"
    )
