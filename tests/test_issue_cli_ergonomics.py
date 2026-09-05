"""Bounded, discoverable search output and measured MCP compatibility."""

from __future__ import annotations

import pytest

from tests.conftest import invoke_cli, parse_json_output


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_grep_rejects_nonpositive_limits_before_indexing(cli_runner, tmp_path, limit):
    result = invoke_cli(cli_runner, ["grep", "x", "--max-results", limit], cwd=tmp_path)
    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert not (tmp_path / ".roam").exists()


@pytest.mark.parametrize("limit_flag", ["--max-results", "-n"])
def test_grep_long_limit_and_group_disclosure(cli_runner, indexed_project, limit_flag):
    result = invoke_cli(
        cli_runner,
        ["grep", "def", limit_flag, "1", "--group-by", "symbol"],
        cwd=indexed_project,
        json_mode=True,
    )
    data = parse_json_output(result, "grep")
    summary = data["summary"]
    assert summary["total"] > 1
    assert summary["shown"] == len(data["matches"]) == 1
    assert summary["omitted_matches"] == summary["total"] - 1
    assert summary["shown_groups"] == len(data["groups"]) == 1
    assert summary["omitted_groups"] == summary["total_groups"] - 1


def test_grep_filter_is_visible_in_verdict(cli_runner, indexed_project):
    result = invoke_cli(cli_runner, ["grep", "def", "--source-only"], cwd=indexed_project)
    assert result.exit_code == 0
    assert "source files only" in result.output.splitlines()[0]


@pytest.mark.parametrize("profile", ["all", "claude"])
def test_compat_profiles_disclose_installed_protocol_support(profile, tmp_path):
    pytest.importorskip("mcp")
    from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

    from roam.mcp_server import _compat_profile_payload

    protocol = _compat_profile_payload(profile, str(tmp_path))["protocol"]
    assert protocol["available"] is True
    assert protocol["supported_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert protocol["preferred_version"] in protocol["supported_versions"]
    assert protocol["support_definition"] == "installed_mcp_sdk_protocol_versions"
    assert protocol["sdk_version"]


def test_installed_sdk_with_incompatible_api_is_not_reported_missing(monkeypatch):
    import sys

    pytest.importorskip("mcp")
    from roam.mcp_server import _protocol_compatibility_payload

    monkeypatch.setitem(sys.modules, "mcp.shared.version", None)
    protocol = _protocol_compatibility_payload()
    assert protocol["available"] is True
    assert protocol["resolution"] == "protocol_api_unavailable"
    assert protocol["partial_success"] is True
    assert protocol["supported_versions"] == []


def test_runtime_and_test_extras_keep_unmigrated_mcp_majors_out():
    from packaging.requirements import Requirement

    from tests._helpers.repo_root import repo_root

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    project = tomllib.loads((repo_root() / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for extra in ("mcp", "dev"):
        requirements = {
            req.name: req for value in project["optional-dependencies"][extra] if (req := Requirement(value))
        }
        assert "3.4.7" in requirements["fastmcp"].specifier
        assert "4.0.2" not in requirements["fastmcp"].specifier
        assert "1.29.1" in requirements["mcp"].specifier
        assert "2.1.1" not in requirements["mcp"].specifier
