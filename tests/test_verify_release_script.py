"""Release-verifier regression tests."""

from __future__ import annotations

import pytest

from scripts import verify_release


@pytest.mark.parametrize(
    ("repo_version", "pypi_version", "expected"),
    [
        ("13.10.0", "13.10.0", True),
        ("13.10", "13.10.0", True),
        ("13.10.0", "13.10", True),
        ("13.10.1", "13.10.0", True),
        ("13.6.1", "13.6", True),
        ("13.10.0", "13.9.4", True),
        ("13.10.0", "13.1.0", False),
        ("14.0.0", "13.10.0", False),
        ("13.9.0", "13.10.0", False),
        ("garbage", "garbage", False),
    ],
)
def test_pypi_freshness_accepts_only_one_publish_step(monkeypatch, repo_version, pypi_version, expected):
    monkeypatch.setattr(verify_release, "_pyproject_version", lambda: repo_version)
    monkeypatch.setattr(verify_release, "_pypi_latest", lambda: pypi_version)

    assert verify_release.check_pypi_freshness() is expected


# Named contract change (2026-11-27). Old: a pre-release in pyproject always
# failed ("13.10.0rc1" against "13.9.0" and against itself). New: PyPI's latest
# never names a pre-release, so a declared pre-release is checked by asking
# PyPI for that exact version; latest is not consulted.
@pytest.mark.parametrize(
    ("repo_version", "served", "expected"),
    [
        ("14.3.0rc1", True, True),
        ("14.3.0rc1", False, False),
        ("14.3.0rc1", None, False),
        ("2.0.0.dev1", True, True),
    ],
)
def test_a_declared_pre_release_is_checked_by_its_exact_version(monkeypatch, repo_version, served, expected):
    asked = []
    monkeypatch.setattr(verify_release, "_pyproject_version", lambda: repo_version)
    monkeypatch.setattr(verify_release, "_pypi_latest", lambda: pytest.fail("latest read for a pre-release"))
    monkeypatch.setattr(verify_release, "_pypi_serves", lambda v: asked.append(v) or served)

    assert verify_release.check_pypi_freshness() is expected
    assert asked == [repo_version]
