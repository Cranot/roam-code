"""Tests for Fix D — MCP parameter-alias normalization layer.

Closes 6 H findings from ``internal/dogfood/SYNTHESIS-2026-05-12.md`` Pattern
3b (vocabulary mismatch across tools). Verifies that legacy parameter
names (``name``, ``target``, ``file``, ``pattern``) are still accepted at
the MCP dispatch boundary, that they translate to the canonical names
(``symbol``, ``path``, ``query``), and that a deprecation warning is
surfaced inside the returned envelope's ``summary.alias_warnings``.

Runs without ``fastmcp`` installed — the helpers under test
(``_normalize_aliases``, ``_attach_alias_warnings``,
``_wrap_with_alias_normalization``) are pure Python and exercised
directly. Equivalent dispatch tests bypass the FastMCP layer by calling
the wrapper output (a plain callable) the same way FastMCP would.
"""

from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Direct unit tests on the helper functions
# ---------------------------------------------------------------------------


def test_normalize_alias_rewrites_name_to_symbol():
    """Single alias only -> rewrites to canonical, emits one warning."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases("roam_uses", {"name": "handleSave"}, accepted={"symbol"})
    assert out == {"symbol": "handleSave"}
    assert len(warns) == 1
    # The deprecation message must name BOTH the alias (so the agent learns
    # what NOT to send) and the canonical (so it learns the migration target).
    assert "name" in warns[0]
    assert "symbol" in warns[0]
    assert "deprecated" in warns[0]


def test_normalize_alias_rewrites_target_to_symbol():
    """``target`` alias also resolves to ``symbol``."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases("roam_preflight", {"target": "open_db"}, accepted={"symbol"})
    assert out == {"symbol": "open_db"}
    assert len(warns) == 1
    assert "target" in warns[0]


def test_normalize_alias_rewrites_file_to_path():
    """``file`` alias resolves to ``path``."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases("roam_effects", {"file": "src/auth.py"}, accepted={"path"})
    assert out == {"path": "src/auth.py"}
    assert len(warns) == 1
    assert "file" in warns[0]


def test_normalize_alias_rewrites_pattern_to_query():
    """``pattern`` alias resolves to ``query``."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases("roam_search_symbol", {"pattern": "login"}, accepted={"query"})
    assert out == {"query": "login"}
    assert "pattern" in warns[0]


def test_normalize_alias_warns_on_both_supplied():
    """Both canonical + alias supplied -> canonical wins, alias dropped, warn."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_uses",
        {"name": "wrong_value", "symbol": "right_value"},
        accepted={"symbol"},
    )
    # Canonical wins, alias is dropped.
    assert out == {"symbol": "right_value"}
    assert len(warns) == 1
    assert "ignoring" in warns[0]
    assert "name" in warns[0]


def test_normalize_alias_canonical_only_no_warning():
    """Canonical name only -> no rewrite, no warning."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases("roam_uses", {"symbol": "foo"}, accepted={"symbol"})
    assert out == {"symbol": "foo"}
    assert warns == []


def test_normalize_alias_skips_when_canonical_not_accepted():
    """If the tool doesn't declare the canonical (e.g. ``target`` on a tool
    that takes a git ref, not a symbol), the alias must NOT be translated."""
    from roam.mcp_server import _normalize_aliases

    # ``breaking_changes(target="HEAD~1")`` — target here is a git ref, not
    # a symbol. ``symbol`` is NOT in accepted, so the alias is left alone.
    out, warns = _normalize_aliases(
        "roam_breaking_changes",
        {"target": "HEAD~1"},
        accepted={"some_other_param"},  # no symbol/path/query
    )
    assert out == {"target": "HEAD~1"}
    assert warns == []


def test_normalize_alias_preserves_unrelated_kwargs():
    """Non-alias kwargs pass through untouched."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_uses",
        {"name": "foo", "full": True, "root": "/tmp"},
        accepted={"symbol"},
    )
    assert out == {"symbol": "foo", "full": True, "root": "/tmp"}
    assert len(warns) == 1


def test_normalize_alias_multiple_concepts_in_one_call():
    """A tool that accepts both ``symbol`` and ``path`` should normalize
    both aliases independently in one call."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_effects",
        {"target": "open_db", "file": "src/auth.py"},
        accepted={"symbol", "path"},
    )
    assert out == {"symbol": "open_db", "path": "src/auth.py"}
    assert len(warns) == 2


# ---------------------------------------------------------------------------
# _attach_alias_warnings — envelope surfacing
# ---------------------------------------------------------------------------


def test_attach_alias_warnings_creates_summary_when_missing():
    from roam.mcp_server import _attach_alias_warnings

    env = {"command": "roam_uses", "data": []}
    out = _attach_alias_warnings(env, ["foo deprecated; use bar"])
    assert out["summary"]["alias_warnings"] == ["foo deprecated; use bar"]


def test_attach_alias_warnings_appends_to_existing_list():
    from roam.mcp_server import _attach_alias_warnings

    env = {
        "summary": {
            "verdict": "ok",
            "alias_warnings": ["prior warning"],
        },
    }
    out = _attach_alias_warnings(env, ["new warning"])
    assert out["summary"]["alias_warnings"] == ["prior warning", "new warning"]


def test_attach_alias_warnings_noop_on_empty_list():
    """No warnings -> result returned unchanged."""
    from roam.mcp_server import _attach_alias_warnings

    env = {"summary": {"verdict": "ok"}}
    out = _attach_alias_warnings(env, [])
    # Summary must be unchanged, no spurious alias_warnings key inserted.
    assert "alias_warnings" not in out["summary"]


def test_attach_alias_warnings_noop_on_non_dict():
    """Non-dict result (None, etc.) passes through unchanged."""
    from roam.mcp_server import _attach_alias_warnings

    assert _attach_alias_warnings(None, ["x"]) is None
    assert _attach_alias_warnings("string-result", ["x"]) == "string-result"


# ---------------------------------------------------------------------------
# _wrap_with_alias_normalization — end-to-end dispatch
# ---------------------------------------------------------------------------


def test_wrapper_translates_name_to_symbol_at_dispatch():
    """Build a tool that takes ``symbol``, wrap it, then call with legacy
    ``name=`` — verify the inner function sees ``symbol=`` and a warning
    is appended to the envelope."""
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str = "", root: str = ".") -> dict:
        return {
            "command": "fake_tool",
            "data": {"received_symbol": symbol, "received_root": root},
        }

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(name="handleSave")
    assert result["data"]["received_symbol"] == "handleSave"
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1
    assert "name" in warns[0] and "symbol" in warns[0]


def test_wrapper_translates_target_to_symbol_at_dispatch():
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str = "", root: str = ".") -> dict:
        return {"command": "fake_tool", "data": {"received_symbol": symbol}}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(target="open_db")
    assert result["data"]["received_symbol"] == "open_db"
    assert "target" in result["summary"]["alias_warnings"][0]


def test_wrapper_translates_file_to_path_at_dispatch():
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(path: str = "", root: str = ".") -> dict:
        return {"command": "fake_tool", "data": {"received_path": path}}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(file="src/auth.py")
    assert result["data"]["received_path"] == "src/auth.py"
    assert "file" in result["summary"]["alias_warnings"][0]


def test_wrapper_canonical_name_unchanged_no_warning():
    """Canonical kwarg supplied — no rewrite, no alias_warnings key."""
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str = "", root: str = ".") -> dict:
        return {
            "command": "fake_tool",
            "summary": {"verdict": "ok"},
            "data": {"received_symbol": symbol},
        }

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(symbol="open_db")
    assert result["data"]["received_symbol"] == "open_db"
    # No alias used, so summary must not have an alias_warnings key.
    assert "alias_warnings" not in result["summary"]


def test_wrapper_both_supplied_prefers_canonical():
    """Both ``name`` and ``symbol`` supplied — canonical wins, alias is
    dropped, warning surfaced. The agent gets explicit feedback that its
    legacy key was ignored."""
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str = "", root: str = ".") -> dict:
        return {"data": {"received_symbol": symbol}}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(name="wrong", symbol="right")
    assert result["data"]["received_symbol"] == "right"
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1
    assert "ignoring" in warns[0]


def test_wrapper_skips_tool_without_canonical_concept():
    """A tool whose params don't include any canonical concept name is
    returned unwrapped — no behaviour change for unrelated tools."""
    from roam.mcp_server import _wrap_with_alias_normalization

    def unrelated_tool(staged: bool = False, depth: int = 2) -> dict:
        return {"ok": True}

    wrapped = _wrap_with_alias_normalization("unrelated", unrelated_tool)
    # Same object — no wrapping took place.
    assert wrapped is unrelated_tool


def test_wrapper_exposes_alias_in_synthesised_signature():
    """The wrapper must advertise BOTH the canonical and the alias in its
    ``__signature__`` so FastMCP / Pydantic schema generation lists both
    spellings on the public tool surface."""
    import inspect

    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str = "", root: str = ".") -> dict:
        return {}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    sig = inspect.signature(wrapped)
    param_names = set(sig.parameters.keys())
    assert "symbol" in param_names  # canonical
    assert "name" in param_names  # alias
    assert "target" in param_names  # alias
    # Alias must be optional so a client sending neither doesn't fail
    # schema validation at FastMCP before the wrapper can run.
    assert sig.parameters["name"].default is None


def test_wrapper_demotes_required_canonical_to_optional():
    """When the inner function's canonical is required (no default), the
    wrapper must demote it to optional in the synthesised signature.
    Otherwise a client sending only the legacy alias would fail FastMCP
    schema validation before the wrapper translates."""
    import inspect

    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(symbol: str, root: str = ".") -> dict:  # symbol required
        return {"data": symbol}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    sig = inspect.signature(wrapped)
    # ``symbol`` is now optional with default "".
    assert sig.parameters["symbol"].default == ""


def test_wrapper_async_path():
    """Async tools must also be wrapped. The wrapper detects coroutine
    functions and preserves async-ness."""
    import asyncio
    import inspect

    from roam.mcp_server import _wrap_with_alias_normalization

    async def fake_async(symbol: str = "", root: str = ".") -> dict:
        return {"data": {"received_symbol": symbol}}

    wrapped = _wrap_with_alias_normalization("fake_async", fake_async)
    assert inspect.iscoroutinefunction(wrapped)

    result = asyncio.run(wrapped(name="async_target"))
    assert result["data"]["received_symbol"] == "async_target"
    assert "name" in result["summary"]["alias_warnings"][0]


# ---------------------------------------------------------------------------
# End-to-end: registered tools accept aliases
# ---------------------------------------------------------------------------


def test_renamed_tool_signature_is_canonical():
    """After Fix D rename: ``roam_uses`` accepts kwarg ``symbol`` (canonical).
    Legacy ``name`` is supported transparently via the wrapper."""
    import inspect

    from roam.mcp_server import roam_uses

    sig = inspect.signature(roam_uses)
    # When fastmcp is not installed (mcp is None), @_tool returns the bare
    # function — its signature has ``symbol`` only. When fastmcp IS
    # installed, the wrapper exposes both. Either way, ``symbol`` is
    # present.
    assert "symbol" in sig.parameters


def test_renamed_tool_callable_with_canonical_kwarg():
    """Direct call with canonical kwarg works."""
    from roam.mcp_server import roam_uses

    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_uses", "data": []}
        roam_uses(symbol="open_db")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["uses", "open_db"]


def test_renamed_preflight_callable_with_canonical_kwarg():
    """Direct call with canonical kwarg ``symbol`` works for preflight."""
    from roam.mcp_server import preflight

    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_preflight", "data": []}
        preflight(symbol="open_db")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["preflight", "open_db"]


def test_renamed_effects_callable_with_canonical_kwargs():
    """Direct call with canonical ``symbol`` + ``path`` works."""
    from roam.mcp_server import effects

    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_effects", "data": []}
        effects(symbol="open_db", path="src/auth.py")
        actual_args = mock.call_args[0][0]
        # Args order: ["effects", symbol, "--path", path]  (W1099: --file is deprecated alias)
        assert actual_args == ["effects", "open_db", "--path", "src/auth.py"]
