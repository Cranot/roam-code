"""W160 — three false-positive filters for the orphan-imports detector.

The 212-eval dogfood audit on roam-code itself surfaced ~344 orphan-imports
findings, of which ~290 (85%) were noise from three known sources:

* pytest-auto-discovered ``conftest`` modules (218 / 63%) — pytest injects
  ``conftest.py`` at collection time; no real import statement exists.
* optional dependencies guarded by ``try: import X; except ImportError:``
  (~50) — the import is EXPECTED to fail in some environments.
* relative imports like ``.base`` / ``.generic_lang`` (24) — the
  regex-based detector captured the dotted fragment but never resolved
  it back to the importing file's package.

This module covers all three filters plus regression tests that genuine
broken imports (truly-missing top-level packages) STILL surface as
orphans. The unit-level helpers (``_is_conftest_path``,
``_optional_import_line_set``, ``_resolve_relative_import``) are
exercised directly so the truth tables stay decoupled from the full
scan pipeline; the end-to-end CliRunner test covers the integrated
behaviour.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_orphan_imports import (
    _is_conftest_path,
    _optional_import_line_set,
    _resolve_relative_import,
)
from tests.conftest import make_src_project as _make_project


# ---------------------------------------------------------------------------
# Unit: _is_conftest_path
# ---------------------------------------------------------------------------


def test_conftest_dotted_resolves_when_file_exists(tmp_path):
    """``from tests.conftest import …`` resolves when tests/conftest.py exists."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("# marker", encoding="utf-8")
    assert _is_conftest_path("tests.conftest", tmp_path) is True


def test_conftest_dotted_unresolved_when_file_absent(tmp_path):
    """Dotted conftest with no matching file is unresolved."""
    assert _is_conftest_path("tests.conftest", tmp_path) is False


def test_conftest_bare_resolves_from_test_file_dir(tmp_path):
    """``import conftest`` from ``tests/test_x.py`` finds the sibling conftest.

    Pytest puts the test's directory on ``sys.path``, so ``import conftest``
    walks up from the importing file looking for the nearest
    ``conftest.py``. Mirror that walk.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("# marker", encoding="utf-8")
    importing = tmp_path / "tests" / "test_x.py"
    importing.write_text("import conftest", encoding="utf-8")
    assert _is_conftest_path("conftest", tmp_path, importing) is True


def test_conftest_bare_unresolved_when_no_conftest_anywhere(tmp_path):
    """Bare ``import conftest`` with no conftest.py in the tree is unresolved."""
    importing = tmp_path / "src" / "module.py"
    importing.parent.mkdir(parents=True)
    importing.write_text("import conftest", encoding="utf-8")
    assert _is_conftest_path("conftest", tmp_path, importing) is False


def test_conftest_ignored_for_non_conftest_paths(tmp_path):
    """The helper only fires on names ending in ``conftest``."""
    assert _is_conftest_path("requests", tmp_path) is False
    assert _is_conftest_path("tests.helpers", tmp_path) is False
    assert _is_conftest_path("conftest_helpers", tmp_path) is False


# ---------------------------------------------------------------------------
# Unit: _optional_import_line_set
# ---------------------------------------------------------------------------


def test_optional_import_line_set_catches_import_error(tmp_path):
    """An ``import`` inside ``try / except ImportError`` is tagged optional."""
    source = textwrap.dedent(
        """
        try:
            import numpy
        except ImportError:
            numpy = None
        """
    )
    lines = _optional_import_line_set(source)
    # The ``import numpy`` lives at source line 3 (1-based: blank + try + import).
    assert 3 in lines


def test_optional_import_line_set_catches_module_not_found_error(tmp_path):
    """``except ModuleNotFoundError`` also marks the import as optional."""
    source = textwrap.dedent(
        """
        try:
            import fastmcp
        except ModuleNotFoundError:
            fastmcp = None
        """
    )
    assert 3 in _optional_import_line_set(source)


def test_optional_import_line_set_catches_tuple_handler(tmp_path):
    """``except (ImportError, ModuleNotFoundError)`` catches both names."""
    source = textwrap.dedent(
        """
        try:
            import watchdog
        except (ImportError, ModuleNotFoundError):
            watchdog = None
        """
    )
    assert 3 in _optional_import_line_set(source)


def test_optional_import_line_set_skips_unrelated_handlers(tmp_path):
    """``except ValueError`` does NOT mark imports inside as optional."""
    source = textwrap.dedent(
        """
        try:
            import requests
        except ValueError:
            pass
        """
    )
    assert _optional_import_line_set(source) == set()


def test_optional_import_line_set_handles_syntax_error_gracefully(tmp_path):
    """SyntaxError returns an empty set instead of crashing."""
    # Mismatched paren is a SyntaxError at parse time.
    assert _optional_import_line_set("def foo(:\n    pass") == set()


def test_optional_import_line_set_ignores_imports_outside_try(tmp_path):
    """Imports at module top level are NOT in the optional set."""
    source = textwrap.dedent(
        """
        import os
        try:
            import numpy
        except ImportError:
            numpy = None
        import sys
        """
    )
    lines = _optional_import_line_set(source)
    # Top-level imports (lines 2 and 7) must not be in the set.
    assert 2 not in lines
    assert 7 not in lines
    # The guarded import on line 4 must be.
    assert 4 in lines


# ---------------------------------------------------------------------------
# Unit: _resolve_relative_import
# ---------------------------------------------------------------------------


def test_resolve_relative_import_single_dot(tmp_path):
    """``.base`` from ``pkg/foo.py`` resolves to ``pkg/base.py``."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "base.py").write_text("X = 1", encoding="utf-8")
    importing = pkg / "foo.py"
    importing.write_text("from .base import X", encoding="utf-8")
    target = _resolve_relative_import(".base", importing, tmp_path)
    assert target == pkg / "base.py"


def test_resolve_relative_import_two_dots(tmp_path):
    """``..commands.resolve`` walks up one package level then back down."""
    src = tmp_path / "src" / "roam"
    (src / "index").mkdir(parents=True)
    (src / "commands").mkdir(parents=True)
    (src / "commands" / "resolve.py").write_text("X = 1", encoding="utf-8")
    importing = src / "index" / "indexer.py"
    importing.write_text("from ..commands.resolve import X", encoding="utf-8")
    target = _resolve_relative_import("..commands.resolve", importing, tmp_path)
    assert target == src / "commands" / "resolve.py"


def test_resolve_relative_import_package_directory(tmp_path):
    """A relative import to a package directory resolves to its __init__.py."""
    pkg = tmp_path / "pkg"
    subpkg = pkg / "sub"
    subpkg.mkdir(parents=True)
    (subpkg / "__init__.py").write_text("", encoding="utf-8")
    importing = pkg / "foo.py"
    importing.write_text("from .sub import bar", encoding="utf-8")
    target = _resolve_relative_import(".sub", importing, tmp_path)
    assert target == subpkg / "__init__.py"


def test_resolve_relative_import_unresolvable_returns_none(tmp_path):
    """A relative import to a non-existent sibling returns None."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    importing = pkg / "foo.py"
    importing.write_text("from .missing import X", encoding="utf-8")
    assert _resolve_relative_import(".missing", importing, tmp_path) is None


def test_resolve_relative_import_non_relative_returns_none(tmp_path):
    """A non-relative dotted path is rejected by the helper."""
    importing = tmp_path / "foo.py"
    importing.write_text("import os", encoding="utf-8")
    assert _resolve_relative_import("os", importing, tmp_path) is None


# ---------------------------------------------------------------------------
# End-to-end via CliRunner — the three filters integrated through the scan
# ---------------------------------------------------------------------------


def _orphan_count(proj: Path, runner: CliRunner) -> int:
    """Helper — index then run orphan-imports --json, return the count."""
    import json as _json

    assert runner.invoke(cli, ["index"]).exit_code == 0
    result = runner.invoke(cli, ["--json", "orphan-imports"])
    assert result.exit_code == 0, result.output
    envelope = _json.loads(result.output)
    return envelope["summary"]["count"]


def test_conftest_import_does_not_surface_as_orphan(tmp_path):
    """A ``from tests.conftest import …`` in a real-shape project resolves cleanly."""
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1",
        },
    )
    # Add a conftest + a test file that imports it. Both sit outside ``src/``
    # so we drop them at project root, matching real pytest layouts.
    (proj / "tests").mkdir()
    (proj / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "tests" / "conftest.py").write_text(
        "# pytest auto-discovers this file\n", encoding="utf-8"
    )
    (proj / "tests" / "test_thing.py").write_text(
        textwrap.dedent(
            """
            from tests.conftest import nothing  # noqa: F401
            import conftest  # noqa: F401

            def test_pass():
                assert True
            """
        ),
        encoding="utf-8",
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        before = _orphan_count(proj, runner)
        # No orphans about conftest — the two imports above must resolve.
        # Other random orphans on the fixture should still be zero.
        assert before == 0, (
            f"expected 0 orphans; got {before} (likely conftest filter regressed)"
        )
    finally:
        os.chdir(old_cwd)


def test_optional_import_inside_try_except_does_not_surface(tmp_path):
    """An ``import numpy`` inside ``try: ... except ImportError:`` is filtered."""
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/opt.py": textwrap.dedent(
                """
                try:
                    import numpy  # type: ignore
                except ImportError:
                    numpy = None
                """
            ),
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        count = _orphan_count(proj, runner)
        # If numpy is actually installed in the test venv, this assertion
        # remains correct (the import resolves via importlib too). If not,
        # the try/except filter is what makes it 0.
        assert count == 0, (
            f"expected 0 orphans; got {count} (optional-import filter regressed)"
        )
    finally:
        os.chdir(old_cwd)


def test_relative_import_to_sibling_does_not_surface(tmp_path):
    """``from .base import X`` resolves to a sibling source file."""
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/base.py": "X = 1",
            "pkg/consumer.py": "from .base import X\n",
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        count = _orphan_count(proj, runner)
        assert count == 0, (
            f"expected 0 orphans; got {count} (relative-import filter regressed)"
        )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Regression: real orphans still surface
# ---------------------------------------------------------------------------


def test_genuinely_missing_package_still_surfaces(tmp_path):
    """A truly broken ``import not_a_real_pkg_xyzzy`` MUST still be reported.

    The three filters must drop noise without eating signal. If a future
    refactor over-broadens conftest/optional/relative detection, this
    regression test catches it.
    """
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/consumer.py": "import not_a_real_pkg_xyzzy_w160\n",
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        import json as _json

        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "orphan-imports"])
        assert result.exit_code == 0, result.output
        envelope = _json.loads(result.output)
        modules = {o["value"]["module"] for o in envelope["orphans"]}
        assert "not_a_real_pkg_xyzzy_w160" in modules
    finally:
        os.chdir(old_cwd)


def test_conftest_filter_does_not_swallow_real_orphans(tmp_path):
    """A real orphan in the same file as conftest imports still surfaces."""
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/foo.py": "X = 1",
        },
    )
    (proj / "tests").mkdir()
    (proj / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (proj / "tests" / "test_mix.py").write_text(
        textwrap.dedent(
            """
            from tests.conftest import foo  # filtered
            import not_a_real_pkg_xyzzy_w160_b  # surfaces

            def test_pass():
                assert True
            """
        ),
        encoding="utf-8",
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        import json as _json

        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "orphan-imports"])
        assert result.exit_code == 0, result.output
        envelope = _json.loads(result.output)
        modules = [o["value"]["module"] for o in envelope["orphans"]]
        # The genuinely-missing import is still flagged.
        assert "not_a_real_pkg_xyzzy_w160_b" in modules
        # The conftest import is NOT.
        assert "tests.conftest" not in modules
        assert "conftest" not in modules
    finally:
        os.chdir(old_cwd)


def test_optional_filter_does_not_swallow_unguarded_imports(tmp_path):
    """An unguarded ``import not_a_real_pkg`` near a try/except is NOT filtered."""
    proj = _make_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mixed.py": textwrap.dedent(
                """
                try:
                    import numpy  # filtered
                except ImportError:
                    numpy = None

                import not_a_real_pkg_xyzzy_w160_c  # surfaces
                """
            ),
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        import json as _json

        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "orphan-imports"])
        assert result.exit_code == 0, result.output
        envelope = _json.loads(result.output)
        modules = [o["value"]["module"] for o in envelope["orphans"]]
        assert "not_a_real_pkg_xyzzy_w160_c" in modules
    finally:
        os.chdir(old_cwd)
