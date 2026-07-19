from __future__ import annotations

import re

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI lane
    import tomli as tomllib

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
WORKFLOW = ROOT / ".github" / "workflows" / "roam-ci.yml"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"

UV_ACTION_SHA = "11f9893b081a58869d3b5fccaea48c9e9e46f990"
UV_VERSION = "0.11.29"
PIP_AUDIT_VERSION = "2.10.1"
SUPPORTED_PYTHONS = {"3.10", "3.11", "3.12", "3.13"}


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

    assert f"astral-sh/setup-uv@{UV_ACTION_SHA}" in text
    assert f'version: "{UV_VERSION}"' in text
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
    assert text.count("uv sync --locked --no-default-groups") >= 6
    assert 'UV_LOCKED: "1"' in text
    assert 'UV_PYTHON_DOWNLOADS: "never"' in text
    assert "uv export \\" in text
    assert "--no-emit-project" in text
    assert "--require-hashes" in text
    assert "uv pip sync" in text


def test_ci_runner_platform_is_pinned_to_the_locked_wheel_abi() -> None:
    text = _workflow()

    assert "ubuntu-latest" not in text
    assert text.count("runs-on: ubuntu-24.04") == 7


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

    assert pyproject["dependency-groups"]["ci"] == [
        f"pip-audit=={PIP_AUDIT_VERSION}",
        "pypdf==6.14.2",
        "setuptools==83.0.0",
        "wheel==0.47.0",
    ]
    assert pyproject["dependency-groups"]["ci-fallback"] == [
        "pytest==9.0.3",
        "pytest-asyncio==1.3.0",
        "pytest-xdist==3.8.0",
    ]

    versions = {package["name"]: package["version"] for package in lock["package"]}
    assert versions["pip-audit"] == PIP_AUDIT_VERSION
    assert versions["pypdf"] == "6.14.2"
    assert versions["setuptools"] == "83.0.0"
    assert versions["wheel"] == "0.47.0"
