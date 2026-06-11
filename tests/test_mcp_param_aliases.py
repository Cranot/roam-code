"""Tests for Fix D — MCP parameter-alias normalization layer.

Closes 6 H findings from `the dogfood synthesis notes` Pattern
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


def test_wrapper_required_canonical_promotes_alias_value():
    """Regression (2026-06-06): a REQUIRED canonical with an alias must still
    promote an alias-only call to the alias VALUE — not silently drop it.

    The wrapper demotes a required canonical (``path``/``symbol`` with no
    default) to ``default=""`` in the synthesised signature so FastMCP accepts
    alias-only calls. FastMCP then fills the canonical with that "" default and
    invokes the wrapper with BOTH the canonical (at "") AND the alias set. The
    promotion rule that distinguishes "FastMCP-filled-with-default" from
    "user-set" reads a defaults snapshot — which previously came from the
    ORIGINAL signature (where the required canonical has NO default), so the
    rule could not fire and the alias value was dropped, leaving path="".
    That made ``roam_deps(file="recipes.py")`` analyse path="" (the first repo
    file, e.g. .dockerignore) instead of recipes.py. The snapshot now comes
    from the merged signature, so the promotion fires.
    """
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(path: str, full: bool = False, root: str = ".") -> dict:  # path required
        return {"data": {"received_path": path}, "summary": {"verdict": "ok"}}

    wrapped = _wrap_with_alias_normalization("roam_deps", fake_tool)

    # Faithful FastMCP shape: client sent only ``file=``; FastMCP fills the
    # demoted canonical ``path`` with its synthesised "" default.
    result = wrapped(path="", file="src/roam/ask/recipes.py", full=True)
    assert result["data"]["received_path"] == "src/roam/ask/recipes.py"
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1
    # Promotion path — "deprecated", NOT the "ignoring" drop path.
    assert "deprecated" in warns[0]
    assert "ignoring" not in warns[0]

    # When BOTH are genuinely user-set (canonical != its "" default), the
    # canonical must still win and the alias be dropped loudly.
    both = wrapped(path="real.py", file="other.py")
    assert both["data"]["received_path"] == "real.py"
    assert "ignoring" in both["summary"]["alias_warnings"][0]


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


# ---------------------------------------------------------------------------
# ALL-TOOLS property test — the generalization that would have caught the
# 2026-06-06 39-tool alias-drop bug instantly.
#
# The single-tool tests above call ``wrapped(name=X)`` WITHOUT the canonical,
# which hits the safe rule-1 promote path — so they could NOT see the bug. The
# bug only fires when FastMCP fills the DEMOTED canonical with its synthesised
# default (both canonical AND alias present), which is exactly what happens over
# the real MCP wire. This test simulates that fill for EVERY registered @_tool
# with an aliased canonical and asserts the alias VALUE reaches the tool body
# (forwarded to ``_run_roam``), instead of being dropped (→ canonical="" → the
# tool silently analyses the wrong target, e.g. roam_deps→.dockerignore).
# ---------------------------------------------------------------------------


def _discover_tool_fn_names() -> list[tuple[str, str]]:
    """AST-walk mcp_server.py → [(tool_name, fn_name)] for each @_tool."""
    import ast
    import pathlib

    import roam.mcp_server as m

    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "_tool":
                tname = next(
                    (
                        kw.value.value
                        for kw in dec.keywords
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                if tname:
                    out.append((tname, node.name))
                break
    return out


def _unwrap_to_raw(obj):
    """Follow ``.fn`` / ``__wrapped__`` to the undecorated function."""
    seen: set[int] = set()
    while obj is not None and id(obj) not in seen:
        seen.add(id(obj))
        inner = getattr(obj, "fn", None)
        if callable(inner):
            obj = inner
            continue
        wrapped = getattr(obj, "__wrapped__", None)
        if wrapped is not None:
            obj = wrapped
            continue
        break
    return obj


def test_all_aliased_tools_promote_alias_value_end_to_end():
    """Every @_tool with an aliased canonical must deliver an alias-only call's
    VALUE to the tool body. Regression for the 39-tool alias-drop class.

    Faithfully simulates FastMCP: pass ``canonical=<merged-sig default>`` (what
    FastMCP fills for the demoted required canonical) together with a synthesised
    alias, then assert the alias value reaches ``_run_roam``. Reverting the
    ``canon_defaults``-from-merged-signature fix makes this fail across dozens of
    tools (canonical stays "" → SENTINEL never reaches the args).
    """
    import inspect

    import roam.mcp_server as m
    from roam.mcp_server import _PARAM_ALIASES, _wrap_with_alias_normalization

    CANON = ("symbol", "path", "query", "input_path")
    SENTINEL = "ZZ_alias_promote_sentinel_ZZ"

    tool_fns = _discover_tool_fn_names()
    assert len(tool_fns) >= 50, (
        f"AST discovery found {len(tool_fns)} @_tool wrappers; expected >=50. "
        f"Discovery is broken — this property test would silently pass."
    )

    verified: list[str] = []
    skipped: list[tuple[str, str]] = []
    failures: list[str] = []

    for tname, fname in tool_fns:
        raw = _unwrap_to_raw(getattr(m, fname, None))
        if not callable(raw):
            skipped.append((tname, "not callable"))
            continue
        try:
            rawsig = inspect.signature(raw)
        except (TypeError, ValueError):
            skipped.append((tname, "no signature"))
            continue
        canon = next((c for c in CANON if c in rawsig.parameters), None)
        if canon is None:
            continue  # tool declares no aliased canonical — out of scope

        # Drive the REAL shared wrapper, but over a RECORDER that has the tool's
        # real signature — so we assert exactly what the wrapper hands the body
        # (the ``canon`` kwarg), independent of how each body USES it (compound
        # recipes / flags / positional). That keeps the test precise: it isolates
        # the wrapper's promotion, which is where the bug lived.
        captured: dict = {}

        def _recorder(*args, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return {"summary": {}}

        _recorder.__signature__ = rawsig  # wrapper introspects this
        _recorder.__name__ = getattr(raw, "__name__", fname)

        wrapped = _wrap_with_alias_normalization(tname, _recorder)
        msig = inspect.signature(wrapped)
        # Pick a GENUINELY-synthesised alias (present in merged sig, absent in raw
        # sig — so it routes through the alias machinery, not a real same-name param).
        alias = next(
            (a for a in _PARAM_ALIASES[canon] if a != canon and a in msig.parameters and a not in rawsig.parameters),
            None,
        )
        if alias is None:
            skipped.append((tname, "no synthesised alias"))
            continue

        # FastMCP fills the demoted canonical with its merged-sig default; pass
        # that + the alias, exactly as the real wire delivers an alias-only call.
        canon_default = msig.parameters[canon].default
        if canon_default is inspect.Parameter.empty:
            canon_default = ""
        try:
            wrapped(**{canon: canon_default, alias: SENTINEL})
        except Exception as e:  # noqa: BLE001
            skipped.append((tname, f"wrapper raised {type(e).__name__}"))
            continue

        got = captured.get(canon)
        if got == SENTINEL:
            verified.append(tname)
        elif got == canon_default:
            failures.append(f"{tname}: alias '{alias}'='{SENTINEL}' DROPPED — body got {canon}={got!r}")
        else:
            skipped.append((tname, f"unexpected {canon}={got!r}"))

    assert not failures, "alias VALUE was DROPPED (tool would analyse the wrong target) for:\n  " + "\n  ".join(
        failures
    )
    assert len(verified) >= 25, (
        f"only verified alias-promotion end-to-end for {len(verified)} tools "
        f"(expected >=25); the property test is under-covering. skipped sample: "
        f"{skipped[:12]}"
    )


def test_all_aliased_tools_both_set_canonical_wins():
    """Complement to the promotion property: when BOTH the canonical (user-set to a
    NON-default value) AND a legacy alias are supplied, the canonical VALUE must
    win and the alias be dropped (rule 2b), for every aliased tool.

    The promotion test guards the "alias dropped when it should be promoted"
    failure (rule 1/2a). This guards the opposite — an alias WRONGLY overriding an
    explicit canonical. A regression in _normalize_aliases' branch logic could
    break either direction; both are now property-tested across the whole surface.
    """
    import inspect

    import roam.mcp_server as m
    from roam.mcp_server import _PARAM_ALIASES, _wrap_with_alias_normalization

    CANON = ("symbol", "path", "query", "input_path")
    REAL = "ZZ_canonical_user_value_ZZ"
    WRONG = "ZZ_alias_should_lose_ZZ"

    tool_fns = _discover_tool_fn_names()
    assert len(tool_fns) >= 50

    verified: list[str] = []
    skipped: list[tuple[str, str]] = []
    failures: list[str] = []

    for tname, fname in tool_fns:
        raw = _unwrap_to_raw(getattr(m, fname, None))
        if not callable(raw):
            continue
        try:
            rawsig = inspect.signature(raw)
        except (TypeError, ValueError):
            continue
        canon = next((c for c in CANON if c in rawsig.parameters), None)
        if canon is None:
            continue

        captured: dict = {}

        def _recorder(*args, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return {"summary": {}}

        _recorder.__signature__ = rawsig
        _recorder.__name__ = getattr(raw, "__name__", fname)

        wrapped = _wrap_with_alias_normalization(tname, _recorder)
        msig = inspect.signature(wrapped)
        alias = next(
            (a for a in _PARAM_ALIASES[canon] if a != canon and a in msig.parameters and a not in rawsig.parameters),
            None,
        )
        if alias is None:
            skipped.append((tname, "no synthesised alias"))
            continue

        try:
            wrapped(**{canon: REAL, alias: WRONG})
        except Exception as e:  # noqa: BLE001
            skipped.append((tname, f"wrapper raised {type(e).__name__}"))
            continue

        got = captured.get(canon)
        if got == REAL:
            verified.append(tname)
        elif got == WRONG:
            failures.append(f"{tname}: alias '{alias}' OVERRODE explicit {canon} (body got {got!r})")
        else:
            skipped.append((tname, f"unexpected {canon}={got!r}"))

    assert not failures, "explicit canonical must win when both canonical + alias are set:\n  " + "\n  ".join(failures)
    assert len(verified) >= 25, (
        f"only verified rule-2b for {len(verified)} tools (expected >=25); skipped sample: {skipped[:12]}"
    )
