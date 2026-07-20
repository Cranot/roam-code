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
        ("13.10.0rc1", "13.9.0", False),
        ("13.10.0rc1", "13.10.0rc1", False),
        ("garbage", "garbage", False),
    ],
)
def test_pypi_freshness_accepts_only_one_publish_step(monkeypatch, repo_version, pypi_version, expected):
    monkeypatch.setattr(verify_release, "_pyproject_version", lambda: repo_version)
    monkeypatch.setattr(verify_release, "_pypi_latest", lambda: pypi_version)

    assert verify_release.check_pypi_freshness() is expected
