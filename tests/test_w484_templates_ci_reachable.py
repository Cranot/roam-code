"""W484 — wheel-bundling reachability test for ``src/roam/templates/ci/``.

W471 added a CI auto-trigger workflow template (SLSA SRC-L3 VSA emit). The
``roam ci-setup`` command materializes these templates into a target repo,
so every file under ``src/roam/templates/ci/`` MUST be reachable via
``importlib.resources.files("roam.templates.ci")`` once roam is installed
from a wheel. ``pyproject.toml`` declares ``"roam.templates.ci" = ["*"]``
in ``[tool.setuptools.package-data]`` — this test guards that declaration
against regression.

W664 lint corollary: package-data without an ``__init__.py`` is invisible
to the wheel under namespace-package resolution. This test asserts the
``__init__.py`` marker file is present and reachable.

The authoritative enumeration is ``importlib.resources`` — i.e. the wheel
contract that ``roam ci-setup`` actually depends on at runtime. Using
``Path(__file__).parent`` for enumeration would falsely pass under
editable installs even when the wheel itself drops files. The minimum
expected template set is asserted explicitly so the test fails closed if
``importlib.resources`` returns an empty directory.

LAW 4 anchor terminals used: ``templates``, ``files``, ``markers``, ``paths``.
"""

from __future__ import annotations

import importlib.resources

import pytest

_TEMPLATES_CI_MODULE = "roam.templates.ci"

# Minimum template files that MUST ship — guards against the failure mode
# where ``importlib.resources.iterdir()`` returns an empty set (wheel built
# without package-data, namespace-package install, etc.). New templates
# added to the directory are auto-covered by the parametrized test below;
# this set is the floor, not the ceiling.
_REQUIRED_TEMPLATE_FILES = frozenset(
    {
        "Jenkinsfile",
        "agent-review.yml",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
        "gitlab-ci.yml",
    }
)


def _enumerate_resource_template_files() -> list[str]:
    """Return every file reachable via ``importlib.resources`` for the module.

    This is the wheel-installed view — the same one ``roam ci-setup``
    consumes at runtime. Excludes ``__pycache__`` and any compiled
    artefacts (``.pyc``) so the assertion focuses on shippable templates
    and the package marker.
    """
    root = importlib.resources.files(_TEMPLATES_CI_MODULE)
    return sorted(entry.name for entry in root.iterdir() if entry.is_file() and not entry.name.endswith(".pyc"))


def test_w484_templates_ci_package_has_init_marker() -> None:
    """W664 discipline — package-data without ``__init__.py`` is invisible.

    Without an ``__init__.py``, setuptools treats the directory as a
    namespace package, ``importlib.resources.files()`` silently fails to
    find package-data on some installs, and ``roam ci-setup`` ships empty
    templates. The marker file MUST be reachable via the wheel.
    """
    resource_init = importlib.resources.files(_TEMPLATES_CI_MODULE).joinpath("__init__.py")
    assert resource_init.is_file(), (
        f"Missing __init__.py via importlib.resources in {_TEMPLATES_CI_MODULE}. "
        "Wheel install dropped the package marker — package-data is invisible "
        "to the wheel without an __init__.py per W664."
    )


def test_w484_templates_ci_required_files_reachable() -> None:
    """The baseline template set MUST be reachable via the wheel.

    Guards against the empty-directory failure mode where parametrization
    over an empty iterable would silently collect zero tests and pass.
    """
    reachable = set(_enumerate_resource_template_files())
    missing = _REQUIRED_TEMPLATE_FILES - reachable
    assert not missing, (
        f"Required templates missing from {_TEMPLATES_CI_MODULE} wheel view: "
        f"{sorted(missing)}. Check [tool.setuptools.package-data] in "
        "pyproject.toml — the 'roam.templates.ci = [\"*\"]' glob must cover "
        "these filenames, and the package MUST contain an __init__.py."
    )


@pytest.mark.parametrize(
    "template_filename",
    _enumerate_resource_template_files(),
)
def test_w484_template_file_reachable_via_importlib_resources(
    template_filename: str,
) -> None:
    """Every file enumerated by the wheel view is a real reachable file.

    Uses ``importlib.resources.files(...)`` rather than ``Path(__file__)``
    so the assertion holds for zipped wheels and namespace-package
    installs — the same contract ``roam ci-setup`` depends on at runtime.
    New templates dropped into ``src/roam/templates/ci/`` are auto-covered
    via the iterdir() enumeration above.
    """
    resource = importlib.resources.files(_TEMPLATES_CI_MODULE).joinpath(template_filename)
    assert resource.is_file(), (
        f"Template '{template_filename}' is enumerated under "
        f"importlib.resources('{_TEMPLATES_CI_MODULE}') but does NOT "
        "resolve to a regular file — the wheel layout is corrupt."
    )
