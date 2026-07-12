"""`roam uses` on a decorator/registry-dispatched framework entrypoint (an MCP
`@_tool` wrapper) must report a distinct
``framework_entrypoint`` state instead of a misleading ``no_consumers``.

A framework entrypoint has zero *static* callers by construction -- the runtime
registry invokes it, not a literal ``foo()`` call site -- so reporting it as
"no consumers" is a false-negative on roam's own precision selling point
(``roam uses onboard`` used to report 0 consumers for a live MCP tool; 56/92
``@_tool`` functions were affected). The classifier is shared with `roam dead`'s
W157 exemption and keys on BOTH the symbol name being in the real
``roam.mcp_server`` tool roster AND the file being ``mcp_server.py``.
"""

from __future__ import annotations

import pytest

from roam.analysis.framework_entrypoints import is_framework_entrypoint
from tests.conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)


@pytest.fixture
def mcp_uses_project(tmp_path):
    """A project whose ``mcp_server.py`` defines a real MCP-tool-named symbol
    (``onboard``, zero callers) alongside a non-tool helper of the same file.

    The classifier reads the *real* roam tool roster, so ``onboard`` classifies
    as a framework entrypoint while ``ordinary_helper`` does not -- exercising
    both branches of the shared name+file check.
    """
    proj = tmp_path / "mcp_uses_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "mcp_server.py").write_text(
        "def onboard():\n    return 'welcome'\n\n\ndef ordinary_helper():\n    return 42\n"
    )
    git_init(proj)
    index_in_process(proj)
    return proj


def test_is_framework_entrypoint_two_axis():
    # Both axes required: a real tool name AND file basename mcp_server.py.
    assert is_framework_entrypoint("onboard", "src/roam/mcp_server.py") is True
    assert is_framework_entrypoint("onboard", "src/roam/other.py") is False
    assert is_framework_entrypoint("not_a_real_tool_xyz", "src/roam/mcp_server.py") is False
    assert is_framework_entrypoint("", "src/roam/mcp_server.py") is False


def test_uses_reports_framework_entrypoint_for_mcp_tool(cli_runner, mcp_uses_project, monkeypatch):
    monkeypatch.chdir(mcp_uses_project)
    result = invoke_cli(cli_runner, ["uses", "onboard"], cwd=mcp_uses_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, "uses")
    summary = data.get("summary", {})
    assert summary.get("state") == "framework_entrypoint", summary
    assert summary.get("registered_via") == "framework_dispatch"
    assert "framework entrypoint" in summary.get("verdict", "")


def test_uses_still_reports_no_consumers_for_ordinary_symbol(cli_runner, mcp_uses_project, monkeypatch):
    # Regression: the exemption is name-specific, not a blanket pass for
    # everything in a file called mcp_server.py.
    monkeypatch.chdir(mcp_uses_project)
    result = invoke_cli(cli_runner, ["uses", "ordinary_helper"], cwd=mcp_uses_project, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, "uses")
    summary = data.get("summary", {})
    assert summary.get("state") == "no_consumers", summary
    assert "registered_via" not in summary
