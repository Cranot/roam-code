from __future__ import annotations

import re
import sys
import tomllib
from itertools import product

import pytest
import yaml
from packaging.specifiers import SpecifierSet

from scripts.verify_uv_runtime import EXPECTED_UV_VERSION
from tests._helpers.repo_root import repo_root
from tests.test_roam_guard_non_github_templates_security import _load as _load_workflow

ROOT = repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "roam-ci.yml"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"

UV_ACTION_SHA = "11f9893b081a58869d3b5fccaea48c9e9e46f990"
UV_VERSION = EXPECTED_UV_VERSION
UV_RUNTIME_GUARD = "python -I -S -B scripts/verify_uv_runtime.py"
PIP_AUDIT_VERSION = "2.10.1"
SUPPORTED_PYTHONS = {"3.11", "3.12", "3.13"}


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(text: str, name: str, next_name: str | None) -> str:
    start = text.index(f"  {name}:\n")
    end = len(text) if next_name is None else text.index(f"  {next_name}:\n", start)
    return text[start:end]


def test_every_remote_action_is_commit_pinned_and_uv_is_version_pinned() -> None:
    text = _workflow()
    refs = re.findall(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", text, flags=re.MULTILINE)

    assert refs
    for ref in refs:
        if ref.startswith("./"):
            continue
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), ref

    # One per independently materialized environment: test, dependency-audit,
    # test-no-optional-deps, lint, doc-hygiene, compatibility, wheel-smoke.
    # The `compatibility` job (W1491) took this from 6 to 7.
    assert text.count(f"astral-sh/setup-uv@{UV_ACTION_SHA}") == 7
    assert text.count(f'version: "{UV_VERSION}"') == 7
    assert text.count(UV_RUNTIME_GUARD) == 7
    assert "$(uv --version)" not in text
    assert "astral-sh/setup-uv@v" not in text


def test_supported_test_and_audit_matrices_are_locked_and_identical() -> None:
    text = _workflow()
    test_job = _job(text, "test", "dependency-audit")
    audit_job = _job(text, "dependency-audit", "test-no-optional-deps")

    for job in (test_job, audit_job):
        versions = set(re.findall(r'"(3\.1[0-3])"', job.split("steps:", 1)[0]))
        assert versions == SUPPORTED_PYTHONS
        assert "uv sync --locked --no-default-groups" in job
        assert "--python python" in job
        assert "UV_PYTHON_DOWNLOADS" not in job  # inherited once at workflow scope

    assert "--extra dev" in test_job
    assert "--extra dev --group ci --group ci-fallback --no-install-project" in audit_job
    assert ".venv/bin/python -m pytest" in test_job


def test_audit_is_exact_pinned_and_fail_closed() -> None:
    text = _workflow()
    audit_job = _job(text, "dependency-audit", "test-no-optional-deps")

    assert f"pip-audit {PIP_AUDIT_VERSION}" in audit_job
    for required in (
        "--local",
        "--strict",
        "--vulnerability-service pypi",
        "uv pip check",
        "refusing to audit a vacuous environment",
        "first-party editable project leaked into the dependency audit",
    ):
        assert required in audit_job
    for forbidden in ("--ignore-vuln", "--skip-editable", "continue-on-error", "|| true"):
        assert forbidden not in audit_job


def test_every_ci_environment_comes_from_uv_lock() -> None:
    text = _workflow()

    executable_lines = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [
        line for line in executable_lines if re.search(r"\bpip\s+install\b", line) and "uv pip install" not in line
    ]
    assert text.count("uv sync --locked --no-default-groups") >= 7
    assert 'UV_LOCKED: "1"' in text
    assert 'UV_PYTHON_DOWNLOADS: "never"' in text
    assert "uv export \\" in text
    assert "--no-emit-project" in text
    assert "--require-hashes" in text
    assert "uv pip sync" in text


def test_ci_runner_platform_is_pinned_to_the_locked_wheel_abi() -> None:
    text = _workflow()

    assert "ubuntu-latest" not in text
    # Inspect every job, including new site-only jobs, instead of freezing an
    # unrelated job count. A missing or different runner must still fail.
    jobs = re.split(r"^  ([a-z][\w-]*):[ \t]*$", text.split("\njobs:\n", 1)[1], flags=re.MULTILINE)
    assert len(jobs) > 1, "No workflow jobs were inspected"
    for name, body in zip(jobs[1::2], jobs[2::2]):
        runners = re.findall(r"^    runs-on:[ \t]*([^\n]+)$", body, flags=re.MULTILINE)
        assert runners == ["ubuntu-24.04"], (name, runners)


def test_fallback_lane_stays_minimal_and_locked() -> None:
    text = _workflow()
    fallback = _job(text, "test-no-optional-deps", "lint")

    assert "--group ci-fallback" in fallback
    assert "--extra dev" not in fallback
    for optional_module in ("scipy", "igraph", "leidenalg", "onnxruntime"):
        assert f'"{optional_module}"' in fallback


def test_ci_tool_versions_are_exact_in_project_metadata_and_lock() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))

    assert pyproject["build-system"] == {
        "requires": ["setuptools==84.1.0", "wheel==0.48.0"],
        "build-backend": "setuptools.build_meta",
    }
    assert pyproject["dependency-groups"]["ci"] == [
        f"pip-audit=={PIP_AUDIT_VERSION}",
        # 6.21.4 since CVE-2026-119203..119205 (earlier: 6.20.1 for advisories
        # fixed through 6.19.0; 6.16.1 for CVE-2026-84309..84311).
        # This literal is the point of the test: bumping a pinned CI tool must
        # be a deliberate edit here too, so a version that drifts in pyproject
        # alone fails rather than passing silently. That is also why Dependabot
        # cannot land these on its own -- its pip-ecosystem PRs edit pyproject
        # and nothing else, so they are red by construction and must be
        # reproduced as a pyproject + uv.lock + test change in one commit.
        "pypdf==6.21.4",
        "setuptools==84.1.0",
        "wheel==0.48.0",
    ]
    assert pyproject["dependency-groups"]["ci-fallback"] == [
        "pytest==9.0.3",
        "pytest-asyncio==1.3.0",
        "pytest-xdist==3.8.0",
    ]

    versions = {package["name"]: package["version"] for package in lock["package"]}
    assert versions["pip-audit"] == PIP_AUDIT_VERSION
    assert versions["pypdf"] == "6.21.4"
    assert versions["setuptools"] == "84.1.0"
    assert versions["wheel"] == "0.48.0"


def _python_matrix_versions(matrix, axis, location):
    assert isinstance(matrix, dict), f"{location}: expected a static Python matrix"
    axes = {key: values for key, values in matrix.items() if key not in {"include", "exclude"}}
    includes = matrix.get("include", [])
    excludes = matrix.get("exclude", [])
    assert isinstance(includes, list) and all(isinstance(row, dict) for row in includes), location
    assert isinstance(excludes, list) and all(isinstance(row, dict) and row for row in excludes), location
    assert all(isinstance(values, list) and values for values in axes.values()), location
    assert axis in axes or not axes, f"{location}: missing {axis} matrix axis"
    assert axis in axes or includes, f"{location}: missing Python matrix values"
    assert all(axis in row for row in includes), f"{location}: each include must specify {axis}"
    assert all(row.keys() <= axes.keys() for row in excludes), f"{location}: unknown exclude axis"

    declared = list(axes.get(axis, [])) + [row[axis] for row in includes]
    excluded_versions = [row[axis] for row in excludes if axis in row]
    assert all(
        isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+", version)
        for version in declared + excluded_versions
    ), f"{location}: expected quoted Python minors, found {declared + excluded_versions}"
    versions = set(declared)
    assert versions <= SUPPORTED_PYTHONS, f"{location}: unsupported Python in {sorted(versions)}"

    if not excludes:
        return versions

    # Exclusions can remove only some combinations for a minor, or all of them.
    remaining = {row[axis] for row in includes}
    for values in product(*axes.values()):
        row = dict(zip(axes, values))
        if not any(all(row[key] == value for key, value in excluded.items()) for excluded in excludes):
            remaining.add(row[axis])
            if remaining == versions:
                break
    return remaining


def test_python_support_metadata_and_all_suite_matrices():
    """Contract change: match Python metadata and every Python workflow matrix."""
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert metadata["requires-python"] == ">=3.11"
    requires_python = SpecifierSet(metadata["requires-python"])
    prefix = "Programming Language :: Python :: "
    declared = metadata.get("classifiers")
    assert isinstance(declared, list) and all(isinstance(value, str) for value in declared), (
        "Python classifiers must be a list of strings"
    )
    classifiers = {
        classifier.removeprefix(prefix)
        for classifier in declared
        if classifier.startswith(prefix) and classifier.removeprefix(prefix)[:1].isdigit()
    } - {"3", "3 :: Only"}
    assert classifiers == SUPPORTED_PYTHONS, f"Python classifiers: {sorted(classifiers)}"
    assert all(version in requires_python for version in classifiers)

    required_jobs = {("roam-ci.yml", "test"), ("roam-ci.yml", "dependency-audit")}
    inspected = set()
    directory = ROOT / ".github" / "workflows"
    for path in sorted(directory.iterdir()):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        for name, job in _load_workflow(path)["jobs"].items():
            location = f"{path.name}:{name}"
            matrix = job.get("strategy", {}).get("matrix")
            axes = set()
            if isinstance(matrix, dict) and (
                "python-version" in matrix
                or any(isinstance(row, dict) and "python-version" in row for row in matrix.get("include", []))
            ):
                axes.add("python-version")
            selected_axes = set()
            for step in [job, *job.get("steps", [])]:
                selector = step.get("with", {}).get("python-version")
                if isinstance(selector, str) and "${{" in selector:
                    match = re.fullmatch(
                        r"\$\{\{\s*matrix(?:\.([\w-]+)|\[(['\"])([\w-]+)\2\])\s*}}",
                        selector,
                    )
                    assert match, f"{location}: cannot verify Python selector {selector!r}"
                    selected_axes.add(match[1] or match[3])
            if (path.name, name) in required_jobs:
                assert selected_axes, f"{location}: Python input must select a matrix axis"
            for axis in sorted(axes | selected_axes):
                versions = _python_matrix_versions(matrix, axis, location)
                assert versions == classifiers, (
                    f"{location}: {axis} has {sorted(versions)}, expected {sorted(classifiers)}"
                )
                inspected.add((path.name, name))
    assert required_jobs <= inspected, f"Missing Python matrices: {sorted(required_jobs - inspected)}"


@pytest.mark.parametrize("versions", [["3.10", "3.11"], [], ["3.x"], None])
def test_python_support_contract_refuses_invalid_matrices(tmp_path, monkeypatch, versions):
    """Controls: matrix input cannot disappear, be empty, or name unsupported Python."""
    _write_python_support_fixture(tmp_path, monkeypatch, sorted(SUPPORTED_PYTHONS), versions)
    with pytest.raises(AssertionError, match="roam-ci.yml:test"):
        test_python_support_metadata_and_all_suite_matrices()


def _write_python_support_fixture(tmp_path, monkeypatch, minors, versions, audit_versions=None):
    metadata = tmp_path / "pyproject.toml"
    classifiers = ", ".join(f'"Programming Language :: Python :: {minor}"' for minor in minors)
    metadata.write_text(f'[project]\nrequires-python = ">=3.11"\nclassifiers = [{classifiers}]\n', encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    workflow = {
        "jobs": {
            "test": {
                "strategy": {"matrix": {"python-version": versions}},
                "steps": [
                    {"uses": "actions/setup-python", "with": {"python-version": "${{ matrix.python-version }}"}},
                    {"run": "python -m pytest tests/"},
                ],
            },
            "dependency-audit": {
                "strategy": {
                    "matrix": {
                        "python-version": sorted(SUPPORTED_PYTHONS) if audit_versions is None else audit_versions
                    }
                },
                "steps": [
                    {"uses": "actions/setup-python", "with": {"python-version": "${{ matrix.python-version }}"}},
                    {"run": "pip-audit"},
                ],
            },
        }
    }
    (workflows / "roam-ci.yml").write_text(yaml.safe_dump(workflow), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "PYPROJECT", metadata)


@pytest.mark.parametrize("drift", ["narrow-audit", "old-audit", "missing-classifier"])
def test_python_support_contract_detects_drift(tmp_path, monkeypatch, drift):
    versions = sorted(SUPPORTED_PYTHONS)
    minors = versions[:-1] if drift == "missing-classifier" else versions
    audit_versions = {
        "narrow-audit": ["3.12"],
        "old-audit": ["3.10", *versions],
        "missing-classifier": versions,
    }[drift]
    _write_python_support_fixture(tmp_path, monkeypatch, minors, versions, audit_versions)
    target = "classifiers" if drift == "missing-classifier" else "roam-ci.yml:dependency-audit"
    with pytest.raises(AssertionError, match=target):
        test_python_support_metadata_and_all_suite_matrices()
