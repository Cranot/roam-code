"""The server's own instructions must not send an agent after an impossible action.

`roam_expand_toolset` is a lister. `_should_register_tool` is consulted exactly
once, at registration, so the tool surface is fixed when the server starts and
nothing reachable from inside a session can widen it. The tool's docstring says
"discover", which is accurate; the server instructions used to say it would
"scope tools to your task", which is not -- an agent that believed it spent a
turn on a call that returns an instruction only the human operator can carry
out.

An instruction is a claim about the program, and this one is checked against
the program.
"""

from __future__ import annotations

import inspect

from roam import mcp_server


def _instructions() -> str:
    server = getattr(mcp_server, "mcp", None)
    if server is None:  # fastmcp absent (CLI-only install): read the literal
        source = inspect.getsource(mcp_server)
        start = source.index('"Codebase intelligence for AI coding agents. "')
        return source[start : start + 1200]
    return str(getattr(server, "instructions", "") or "")


def test_the_toolset_filter_is_registration_time_only():
    # The fact the instruction has to respect. If this ever becomes a per-call
    # check, runtime expansion becomes reachable and the wording can change --
    # but then it will be TRUE, which is the point.
    source = inspect.getsource(mcp_server)
    call_sites = source.count("_should_register_tool(")
    definition = source.count("def _should_register_tool(")
    assert call_sites - definition == 1, (
        "the active-tool filter is consulted at more than one site; re-check whether "
        "the surface can now change after startup before trusting the instructions"
    )


def test_the_instructions_do_not_promise_in_session_expansion():
    text = _instructions().lower()
    assert "expand_toolset" in text, "the meta-tool should still be mentioned"
    # The specific false promise, and its close relatives. The tool cannot
    # scope, switch, enable or expand anything from inside a session.
    for claim in ("scope tools to your task", "to expand the toolset", "to switch presets"):
        assert claim not in text, f"instructions promise an action the server cannot perform: {claim!r}"


def test_the_instructions_say_how_a_preset_is_actually_selected():
    # Removing the false promise is not enough on its own -- a reader still
    # needs the true route, or the narrow default surface looks like a dead end.
    text = _instructions()
    assert "ROAM_MCP_PRESET" in text
