"""Doctor checks: does the ENVIRONMENT still match what the SOURCE declares?

Two staleness shapes, both paid for in real hours on 2026-07-26 while the
source was correct throughout:

* Installed PACKAGE VERSIONS drifting from pyproject.toml's declared
  specifiers. The venv held tree-sitter-language-pack==1.6.2 against
  pyproject's declared >=1.13.3,<1.14, so ``has_language()`` returned False,
  every file parsed to zero symbols, and ~18 tests failed -- asymmetrically
  across branches, which impersonated a broken merge and cost a
  three-revision bisect.
* Installed DISTRIBUTION METADATA drifting from pyproject.toml's declared
  version. An editable install's dist-info is written once, at install
  time; bump the version afterward and ``--version`` (and anything else
  reading installed metadata) keeps lying until reinstalled. Observed on a
  sibling project the same day: compile_code-0.1.0.dist-info survived a
  0.2.0 bump.

Both checks are self-scoped: they only activate when ``./pyproject.toml``
actually declares the ``roam-code`` project, so running ``roam doctor``
inside an unrelated project's checkout is a quiet not_applicable rather
than noise about a project it isn't examining.
"""

from __future__ import annotations

import importlib.metadata as md
import os
from pathlib import Path

from roam.commands.cmd_doctor import (
    _ADVISORY_CHECK_NAMES,
    _check_dependency_versions,
    _check_installed_version_drift,
)

# click is a hard, always-installed dependency of roam-code itself (via
# click.testing.CliRunner elsewhere in this suite), so its installed
# version is a stable, host-independent probe -- no mocking required.
_CLICK_VERSION = md.version("click")
_ROAM_CODE_VERSION = md.version("roam-code")


def _write_pyproject(
    tmp: Path,
    *,
    name: str = "roam-code",
    version: str | None = "0.0.0",
    dependencies: list[str] | None = None,
) -> None:
    lines = ["[project]", f'name = "{name}"']
    if version is not None:
        lines.append(f'version = "{version}"')
    deps = dependencies if dependencies is not None else []
    dep_list = ", ".join(f'"{d}"' for d in deps)
    lines.append(f"dependencies = [{dep_list}]")
    (tmp / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_in(tmp: Path, fn):
    prev = os.getcwd()
    try:
        os.chdir(tmp)
        return fn()
    finally:
        os.chdir(prev)


# ---------------------------------------------------------------------------
# _check_dependency_versions
# ---------------------------------------------------------------------------


class TestDependencyVersions:
    def test_no_pyproject_is_not_applicable(self, tmp_path: Path) -> None:
        result = _run_in(tmp_path, _check_dependency_versions)
        assert result["passed"] is True
        assert result.get("_state") == "not_applicable"

    def test_unrelated_project_name_is_not_applicable(self, tmp_path: Path) -> None:
        """A pyproject.toml for a DIFFERENT project must not be compared against."""
        _write_pyproject(tmp_path, name="some-other-project", dependencies=["click>=999.0"])
        result = _run_in(tmp_path, _check_dependency_versions)
        assert result["passed"] is True
        assert result.get("_state") == "not_applicable"

    def test_clean_environment_passes(self, tmp_path: Path) -> None:
        """An installed version that satisfies the declared specifier must not fire."""
        _write_pyproject(tmp_path, dependencies=["click>=0.0.1"])
        result = _run_in(tmp_path, _check_dependency_versions)
        assert result["passed"] is True
        assert result.get("_state") != "not_applicable"

    def test_detects_a_planted_violation(self, tmp_path: Path) -> None:
        """An impossible lower bound on an already-installed package must be reported."""
        _write_pyproject(tmp_path, dependencies=[f"click>={_CLICK_VERSION}.1"])
        # `.1` appended to the real installed version always sorts higher,
        # so the declared bound can never be satisfied by what's installed
        # -- independent of what click version happens to be on this host.
        result = _run_in(tmp_path, _check_dependency_versions)
        assert result["passed"] is False
        assert "click" in result["detail"]
        assert "pip install -e ." in result["detail"]

    def test_environment_marker_false_is_not_a_violation(self, tmp_path: Path) -> None:
        """A requirement whose marker doesn't apply here must be silently skipped."""
        _write_pyproject(
            tmp_path,
            dependencies=["click>=0.0.1", "nonexistent-package-xyz>=1.0; python_version < '3.0'"],
        )
        result = _run_in(tmp_path, _check_dependency_versions)
        assert result["passed"] is True

    def test_is_registered_advisory(self) -> None:
        """Unregistered defaults to BLOCKING (231f1bd5's bug) -- pin the membership."""
        assert "Dependency versions" in _ADVISORY_CHECK_NAMES


# ---------------------------------------------------------------------------
# _check_installed_version_drift
# ---------------------------------------------------------------------------


class TestInstalledVersionDrift:
    def test_no_pyproject_is_not_applicable(self, tmp_path: Path) -> None:
        result = _run_in(tmp_path, _check_installed_version_drift)
        assert result["passed"] is True
        assert result.get("_state") == "not_applicable"

    def test_unrelated_project_name_is_not_applicable(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, name="some-other-project", version="1.2.3")
        result = _run_in(tmp_path, _check_installed_version_drift)
        assert result["passed"] is True
        assert result.get("_state") == "not_applicable"

    def test_no_static_version_is_not_applicable(self, tmp_path: Path) -> None:
        """[project.version] omitted (e.g. dynamic versioning) -- nothing to compare."""
        _write_pyproject(tmp_path, version=None)
        result = _run_in(tmp_path, _check_installed_version_drift)
        assert result["passed"] is True
        assert result.get("_state") == "not_applicable"

    def test_clean_environment_passes(self, tmp_path: Path) -> None:
        """Declared version == installed dist metadata -- must not fire."""
        _write_pyproject(tmp_path, version=_ROAM_CODE_VERSION)
        result = _run_in(tmp_path, _check_installed_version_drift)
        assert result["passed"] is True
        assert result.get("_state") != "not_applicable"

    def test_detects_a_planted_violation(self, tmp_path: Path) -> None:
        """A declared version that differs from installed dist metadata must be reported."""
        drifted = _ROAM_CODE_VERSION + ".drifted-test"
        _write_pyproject(tmp_path, version=drifted)
        result = _run_in(tmp_path, _check_installed_version_drift)
        assert result["passed"] is False
        assert _ROAM_CODE_VERSION in result["detail"]
        assert drifted in result["detail"]
        assert "pip install -e ." in result["detail"]

    def test_is_registered_advisory(self) -> None:
        """Unregistered defaults to BLOCKING (231f1bd5's bug) -- pin the membership."""
        assert "Installed version" in _ADVISORY_CHECK_NAMES
