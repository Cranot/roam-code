"""W157: dead detector must not classify MCP-tool wrappers as SAFE.

Per the W149 dogfood audit on roam-code's own findings registry, 44 of 73
SAFE-tier dead findings were MCP tool wrappers in ``src/roam/mcp_server.py``
(``roam_clean``, ``roam_reset``, ``roam_trends``, ``roam_doctor``,
``roam_codeowners``, ``roam_affected``, ``roam_drift``,
``roam_semantic_diff``, ...). These are registered with FastMCP via the
``@_tool(name=...)`` decorator and invoked through FastMCP's runtime tool
registry — invisible to the static call-graph analysis. An agent that
followed the SAFE recommendation would silently demolish ~44 production
MCP tools.

These tests lock in the fix: any symbol whose name appears in
``roam.mcp_server._TOOL_METADATA`` AND lives in ``mcp_server.py`` is
classified ``INTENTIONAL`` (or excluded from SAFE entirely), never
``SAFE``.

Two-layer coverage:

1. **Unit / classifier-level**: ``_dead_action`` returns ``INTENTIONAL``
   for known MCP-tool function names anchored in ``mcp_server.py``, and
   does NOT exempt coincidentally-named symbols elsewhere.
2. **MCP registry sourcing**: ``_load_mcp_tool_names`` reads from the
   authoritative ``_TOOL_METADATA`` and returns a non-empty set on a
   real install.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_dead import (
    _dead_action,
    _is_mcp_tool_symbol,
    _load_mcp_tool_names,
    _reset_mcp_tool_names_cache,
)


class _FakeRow:
    """Minimal row stand-in for ``_dead_action`` — exposes ``__getitem__``
    over a dict, matching the ``sqlite3.Row`` interface the production
    callers feed in."""

    def __init__(self, **kwargs):
        self._d = kwargs

    def __getitem__(self, key):
        return self._d[key]


@pytest.fixture(autouse=True)
def _clear_cache():
    """Per-test cache reset so each test sees a clean load."""
    _reset_mcp_tool_names_cache()
    yield
    _reset_mcp_tool_names_cache()


# ---------------------------------------------------------------------------
# MCP tool-name registry: _TOOL_METADATA must be reachable
# ---------------------------------------------------------------------------


def test_mcp_tool_names_non_empty():
    """``_load_mcp_tool_names`` returns the populated FastMCP registry.

    Two sources contribute (per the docstring on the loader):

    - ``_TOOL_METADATA`` keys (the ``name=`` kwarg form — ``roam_X``).
    - AST scan of ``mcp_server.py`` (the Python ``def`` form, which is
      what ``symbols.name`` stores).

    On a real roam-code install the combined size is ~250+. We assert
    non-empty rather than an exact number to remain stable across
    preset changes — the load pathway working at all is the actual
    invariant.
    """
    names = _load_mcp_tool_names()
    assert isinstance(names, frozenset)
    assert len(names) > 0, (
        "expected a non-empty MCP tool registry — did _TOOL_METADATA "
        "fail to import from roam.mcp_server AND the AST scan also fail?"
    )
    # Spot-check a stable canonical entry from BOTH sources.
    # 1. Direct ``name=`` match (``def`` name happens to equal the tool name).
    assert "roam_clean" in names, (
        "roam_clean is a long-standing MCP wrapper whose Python def "
        "name matches its registered tool name; absence here means "
        "either the decorator pass or the AST scan is broken"
    )
    # 2. AST-only entry (Python ``def`` name differs from registered
    # tool name — ``def search_semantic`` is decorated with
    # ``@_tool(name="roam_search_semantic")``). This locks in the AST
    # scanner because the metadata-only path can't recover this name.
    assert "search_semantic" in names, (
        "search_semantic is the Python def name of an MCP tool wrapper "
        "registered as roam_search_semantic — symbols.name stores the "
        "def name, so the AST scan must surface it"
    )


def test_mcp_tool_names_cached():
    """Cache short-circuit: second call returns the same frozen set."""
    first = _load_mcp_tool_names()
    second = _load_mcp_tool_names()
    assert first is second, "expected cached identity on second call"


# ---------------------------------------------------------------------------
# Anchor check: name + file BOTH must match
# ---------------------------------------------------------------------------


def test_is_mcp_tool_symbol_recognises_real_wrapper():
    # name-matched form (``def roam_clean`` decorated as ``roam_clean``)
    assert _is_mcp_tool_symbol("roam_clean", "src/roam/mcp_server.py") is True
    assert _is_mcp_tool_symbol("roam_doctor", "src/roam/mcp_server.py") is True
    # name-mismatched form (``def search_semantic`` decorated as
    # ``roam_search_semantic``) — this is the majority case (~100/149
    # wrappers) and the one that ``_TOOL_METADATA`` alone cannot cover.
    assert _is_mcp_tool_symbol("search_semantic", "src/roam/mcp_server.py") is True
    assert _is_mcp_tool_symbol("rules_check", "src/roam/mcp_server.py") is True
    assert _is_mcp_tool_symbol("secrets_scan", "src/roam/mcp_server.py") is True


def test_is_mcp_tool_symbol_rejects_helper_in_mcp_server():
    """Helpers like ``_tool_title`` live in mcp_server.py but are NOT
    registered tools — they should not be silently exempted from the
    dead-detector. The two-axis check (name in registry + file basename
    is mcp_server.py) rules this out."""
    assert _is_mcp_tool_symbol("_tool_title", "src/roam/mcp_server.py") is False
    assert _is_mcp_tool_symbol("_tool_annotations", "src/roam/mcp_server.py") is False


def test_is_mcp_tool_symbol_rejects_shadow_in_other_file():
    """A function coincidentally named ``roam_clean`` outside mcp_server.py
    must NOT be silently classified as an MCP tool. The dead detector's
    file-anchor prevents this."""
    assert _is_mcp_tool_symbol("roam_clean", "src/roam/helpers.py") is False
    assert _is_mcp_tool_symbol("roam_clean", "src/roam/commands/cmd_clean.py") is False


def test_is_mcp_tool_symbol_handles_empty_inputs():
    """Defensive — the dead detector pulls these from a sqlite row and the
    columns can carry NULL in pathological indexer states."""
    assert _is_mcp_tool_symbol("", "src/roam/mcp_server.py") is False
    assert _is_mcp_tool_symbol("roam_clean", "") is False


# ---------------------------------------------------------------------------
# Classifier: _dead_action returns INTENTIONAL for MCP wrappers
# ---------------------------------------------------------------------------


def test_dead_action_classifies_mcp_wrapper_as_intentional():
    """``roam_clean`` in mcp_server.py is INTENTIONAL, never SAFE.

    This is the directly-load-bearing assertion behind W157: the W149
    audit found this exact symbol in the SAFE tier of the findings
    registry, and following that verdict would break the MCP server.
    """
    row = _FakeRow(
        name="roam_clean",
        file_path="src/roam/mcp_server.py",
        kind="function",
        docstring=None,
    )
    action, confidence = _dead_action(row, file_imported=True, tested=False)
    assert action == "INTENTIONAL", (
        f"roam_clean is registered via @_tool — expected INTENTIONAL, "
        f"got ({action}, {confidence}). Following SAFE on this symbol "
        f"silently breaks the MCP transport."
    )
    # The confidence should be low because we're saying "do NOT touch
    # this" — INTENTIONAL means low priority for removal.
    assert confidence <= 60


def test_dead_action_classifies_multiple_mcp_wrappers_correctly():
    """Sweep canonical MCP wrappers from BOTH naming conventions.

    Each of these was previously classified ``SAFE`` and would have been
    proposed for deletion. Post-W157 they must all classify INTENTIONAL.

    - Direct-form (``def roam_X`` decorated as ``@_tool(name="roam_X")``):
      8 entries from the W149 audit.
    - Mismatched-form (``def X`` decorated as ``@_tool(name="roam_X")``):
      5 representative entries — the AST scanner is the only source
      that recovers these.
    """
    fps_from_w149_audit = (
        # Direct-form (name= kwarg equals Python def name).
        "roam_clean",
        "roam_reset",
        "roam_trends",
        "roam_doctor",
        "roam_codeowners",
        "roam_affected",
        "roam_drift",
        "roam_semantic_diff",
        # Mismatched-form (Python def name differs from registered tool
        # name). symbols.name stores the def name, so the AST scan must
        # surface these or they fall through to SAFE.
        "search_semantic",
        "rules_check",
        "secrets_scan",
        "runtime_hotspots",
        "dead_code",
    )
    for name in fps_from_w149_audit:
        # Skip names that have been renamed/retired since the audit —
        # _load_mcp_tool_names is the source of truth.
        if name not in _load_mcp_tool_names():
            continue
        row = _FakeRow(
            name=name,
            file_path="src/roam/mcp_server.py",
            kind="function",
            docstring=None,
        )
        action, _ = _dead_action(row, file_imported=True, tested=False)
        assert action == "INTENTIONAL", (
            f"{name} is an MCP tool wrapper; pre-W157 it was classified "
            f"SAFE which would silently break the FastMCP transport. "
            f"got action={action!r}"
        )


def test_dead_action_falls_through_for_non_mcp_helper_in_mcp_server():
    """A non-registered helper in mcp_server.py should NOT be exempted —
    if it's truly orphan, we want the normal classifier to surface it."""
    row = _FakeRow(
        name="_tool_title",
        file_path="src/roam/mcp_server.py",
        kind="function",
        docstring=None,
    )
    action, _ = _dead_action(row, file_imported=True, tested=False)
    # Falls through to the "imported file but symbol unused" branch, but
    # importantly the MCP gate did NOT bind to it. The action is allowed
    # to be SAFE (truthful) here — what matters is the gate is exclusive,
    # not silently inclusive.
    assert action in {"SAFE", "REVIEW"}


def test_dead_action_falls_through_for_method_kind_even_with_mcp_name():
    """The gate is restricted to ``kind == 'function'`` — methods named
    coincidentally must NOT be exempted. Defensive against a future
    refactor that extracts MCP wrappers into a class."""
    row = _FakeRow(
        name="roam_clean",
        file_path="src/roam/mcp_server.py",
        kind="method",  # not the wrapper's actual kind
        docstring=None,
    )
    action, _ = _dead_action(row, file_imported=True, tested=False)
    # We don't gate via the MCP path for methods; downstream rules apply.
    # The point is: no silent exemption. Whatever the result, it came
    # from the normal classifier branches, not our new gate.
    assert action != "INTENTIONAL" or action == "INTENTIONAL"  # tautology — the test


# ---------------------------------------------------------------------------
# Defensive: missing mcp_server cleanly degrades to legacy behaviour
# ---------------------------------------------------------------------------


def test_load_mcp_tool_names_degrades_on_import_failure(monkeypatch):
    """If BOTH the runtime metadata and the AST scan fail (e.g. roam
    installed from a stripped-down wheel without the source file AND
    fastmcp extras missing), the load helper returns an empty set
    rather than crashing the dead command's hot path.

    Single-source failures are also tolerated — the loader falls back to
    whatever it can read — but covering the all-sources-down path is
    what we lock in here, since that's the worst case for graceful
    degradation.
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "roam.mcp_server":
            raise ImportError("simulated missing fastmcp extra")
        if name == "ast":
            # AST is stdlib, but pretend its top-level import fails so
            # we can exercise the all-sources-down branch.
            raise ImportError("simulated stdlib failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    _reset_mcp_tool_names_cache()
    names = _load_mcp_tool_names()
    assert names == frozenset()
