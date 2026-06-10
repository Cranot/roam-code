"""W303 -- MCP test-surface / world-model wrapper tests.

Wave29 sub-wave 5 adds 5 wrappers in ``src/roam/mcp_server.py`` for the
test-surface cluster: ``roam_coverage_gaps``, ``roam_side_effects``,
``roam_idempotency``, ``roam_causal_graph``, ``roam_tx_boundaries``.

The original W303 plan listed 8 wrappers but W302 already shipped the
three test-surface commands (``test-map`` / ``test-pyramid`` /
``test-scaffold``); the remaining 5 land here.

This module pins:

* each wrapper is registered in ``_TOOL_METADATA`` under the expected name
* each wrapper IS NOT in ``_NO_INDEX_NEEDED`` (they all require an index)
* each wrapper's description carries the W296 ``INDEX_REQUIRED_HINT``
  (auto-appended by ``maybe_decorate_description``)
* each wrapper's CLI argument shape is what the underlying command
  expects (verified by mocking ``_run_roam`` and asserting the args).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _disable_cold_start_guard(monkeypatch):
    """Tests mock ``_run_roam`` directly, so we want the cold-start guard
    to short-circuit to pass-through rather than fire on whatever cwd the
    test runner picks up. ``ROAM_MCP_DISABLE_COLD_START_GUARD`` flips the
    guard to a no-op for the duration of each test (see
    ``roam.mcp_extras.preflight.maybe_cold_start_envelope``).
    """
    monkeypatch.setenv("ROAM_MCP_DISABLE_COLD_START_GUARD", "1")
    yield


# The 5 wrappers shipped by W303.
W303_TOOL_NAMES: tuple[str, ...] = (
    "roam_coverage_gaps",
    "roam_side_effects",
    "roam_idempotency",
    "roam_causal_graph",
    "roam_tx_boundaries",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W303 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W303_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W303 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W303_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 5 wrappers are read-only.

        ``roam_coverage_gaps`` runs gate-coverage analysis without
        touching disk. The four world-model classifiers
        (``side-effects`` / ``idempotency`` / ``causal-graph`` /
        ``tx-boundaries``) are pure read-only detectors that walk the
        indexed graph.
        """
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the test-surface cluster "
            f"only contains analyses and classifiers; no wrapper writes "
            f"to disk."
        )

    @pytest.mark.parametrize("tool_name", W303_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 5 wrappers."""

    @pytest.mark.parametrize("tool_name", W303_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W303 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- every "
            f"command in the test-surface cluster reads the indexed "
            f"graph (call edges, symbol bodies, side-effect detector, "
            f"or the symbols table)."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W303_TOOL_NAMES)
    def test_wrapper_description_carries_cold_start_hint(self, tool_name: str) -> None:
        """W296 hint is auto-appended to every index-gated wrapper."""
        from roam.mcp_extras.preflight import INDEX_REQUIRED_HINT
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert INDEX_REQUIRED_HINT in desc, (
            f"{tool_name} description must end with the W296 hint {INDEX_REQUIRED_HINT!r}; actual description: {desc!r}"
        )


# ---------------------------------------------------------------------------
# Per-wrapper CLI argument shape
# ---------------------------------------------------------------------------


class TestRoamCoverageGapsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_coverage_gaps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default max_depth=8 mirrors the CLI default per LAW 11.
            roam_coverage_gaps()
            mock.assert_called_once_with(["coverage-gaps", "--max-depth", "8"], ".")

    def test_gate_names(self) -> None:
        from roam.mcp_server import roam_coverage_gaps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_coverage_gaps(gate="requireAuth,validateToken")
            args = mock.call_args[0][0]
            assert args[0] == "coverage-gaps"
            assert "--gate" in args and "requireAuth,validateToken" in args

    def test_gate_pattern_and_scope(self) -> None:
        from roam.mcp_server import roam_coverage_gaps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_coverage_gaps(
                gate_pattern="auth|permission|guard",
                scope="app/routes/**",
                entry_pattern="handler|controller",
            )
            args = mock.call_args[0][0]
            assert "--gate-pattern" in args and "auth|permission|guard" in args
            assert "--scope" in args and "app/routes/**" in args
            assert "--entry-pattern" in args and "handler|controller" in args

    def test_preset_auto_detect_and_input_path(self) -> None:
        """W332 canonical ``input_path`` -- ``.roam-gates.yml`` sidecar."""
        from roam.mcp_server import roam_coverage_gaps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_coverage_gaps(
                preset="python",
                auto_detect=True,
                input_path=".roam-gates.yml",
                max_depth=12,
            )
            args = mock.call_args[0][0]
            assert args[0] == "coverage-gaps"
            assert "--preset" in args and "python" in args
            assert "--auto-detect" in args
            assert "--config" in args and ".roam-gates.yml" in args
            assert "--max-depth" in args and "12" in args


class TestRoamSideEffectsArgShape:
    def test_default_invocation_no_symbol(self) -> None:
        from roam.mcp_server import roam_side_effects

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default symbol="" -> CLI scans all per LAW 11; top=50 default.
            roam_side_effects()
            mock.assert_called_once_with(["side-effects", "--top", "50"], ".")

    def test_symbol_filter(self) -> None:
        from roam.mcp_server import roam_side_effects

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_side_effects(symbol="handleSave")
            args = mock.call_args[0][0]
            assert args[:2] == ["side-effects", "handleSave"]
            assert "--top" in args and "50" in args

    def test_kind_filter_and_top(self) -> None:
        from roam.mcp_server import roam_side_effects

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_side_effects(kind="io_write", top=20)
            args = mock.call_args[0][0]
            assert args[0] == "side-effects"
            assert "--kind" in args and "io_write" in args
            assert "--top" in args and "20" in args


class TestRoamIdempotencyArgShape:
    def test_default_invocation_no_symbol(self) -> None:
        from roam.mcp_server import roam_idempotency

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_idempotency()
            mock.assert_called_once_with(["idempotency", "--top", "50"], ".")

    def test_symbol_filter(self) -> None:
        from roam.mcp_server import roam_idempotency

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_idempotency(symbol="createUser")
            args = mock.call_args[0][0]
            assert args[:2] == ["idempotency", "createUser"]

    def test_kind_filter(self) -> None:
        from roam.mcp_server import roam_idempotency

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_idempotency(kind="non_idempotent", top=10)
            args = mock.call_args[0][0]
            assert args[0] == "idempotency"
            assert "--kind" in args and "non_idempotent" in args
            assert "--top" in args and "10" in args


class TestRoamCausalGraphArgShape:
    def test_default_invocation_no_symbol(self) -> None:
        from roam.mcp_server import roam_causal_graph

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default top=20 mirrors the CLI default per LAW 11.
            roam_causal_graph()
            mock.assert_called_once_with(["causal-graph", "--top", "20"], ".")

    def test_symbol_filter(self) -> None:
        from roam.mcp_server import roam_causal_graph

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_causal_graph(symbol="handleSave")
            args = mock.call_args[0][0]
            assert args[:2] == ["causal-graph", "handleSave"]

    def test_kind_filter(self) -> None:
        from roam.mcp_server import roam_causal_graph

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_causal_graph(kind="param_to_effect", top=5)
            args = mock.call_args[0][0]
            assert "--kind" in args and "param_to_effect" in args
            assert "--top" in args and "5" in args


class TestRoamTxBoundariesArgShape:
    def test_default_invocation_no_symbol(self) -> None:
        from roam.mcp_server import roam_tx_boundaries

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default top=30 mirrors the CLI default per LAW 11.
            roam_tx_boundaries()
            mock.assert_called_once_with(["tx-boundaries", "--top", "30"], ".")

    def test_symbol_filter(self) -> None:
        from roam.mcp_server import roam_tx_boundaries

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_tx_boundaries(symbol="transferFunds")
            args = mock.call_args[0][0]
            assert args[:2] == ["tx-boundaries", "transferFunds"]

    def test_classification_filter(self) -> None:
        from roam.mcp_server import roam_tx_boundaries

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_tx_boundaries(classification="unsafe_mutation", top=15)
            args = mock.call_args[0][0]
            assert "--classification" in args and "unsafe_mutation" in args
            assert "--top" in args and "15" in args


class TestRoamDepsArgShape:
    """``multi`` mirrors the CLI ``--multi`` flag (imports+importers+cochange in
    one envelope). Added so codex stops shelling out to ``roam deps --multi`` /
    raw SQL on coupling questions (nav A/B q5)."""

    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_deps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_deps(path="src/auth.py")
            mock.assert_called_once_with(["deps", "src/auth.py"], ".")

    def test_multi_appends_flag(self) -> None:
        from roam.mcp_server import roam_deps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_deps(path="src/auth.py", multi=True)
            args = mock.call_args[0][0]
            assert args[:2] == ["deps", "src/auth.py"]
            assert "--multi" in args

    def test_multi_false_omits_flag(self) -> None:
        from roam.mcp_server import roam_deps

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_deps(path="src/auth.py", full=True, multi=False)
            args = mock.call_args[0][0]
            assert "--full" in args
            assert "--multi" not in args

    def test_exposes_cli_data_flags_parity(self) -> None:
        """roam_deps must expose the `deps` CLI's DATA flags (multi, full) so
        agents reach them over MCP instead of shelling out — the parity gap that
        caused the nav A/B q2 SQL fallback (2026-06-07). Guards against the
        wrapper drifting behind the CLI again."""
        import inspect

        import click

        from roam.cli import cli
        from roam.mcp_server import roam_deps

        cmd = cli.get_command(None, "deps")
        cli_opts = {p.name for p in cmd.params if isinstance(p, click.Option)}
        raw = roam_deps
        for attr in ("fn", "__wrapped__", "func"):
            inner = getattr(raw, attr, None)
            if callable(inner):
                raw = inner
        wrapper_params = set(inspect.signature(raw).parameters)
        for flag in ("multi", "full"):
            assert flag in cli_opts, f"deps CLI unexpectedly lost --{flag}"
            assert flag in wrapper_params, (
                f"roam_deps must expose '{flag}' (MCP/CLI parity gap — agents would shell out for it)"
            )
