"""W300 -- MCP architecture wrapper tests.

Wave29 sub-wave 2 adds 10 wrappers in ``src/roam/mcp_server.py`` for the
architecture cluster: ``roam_clusters``, ``roam_layers``, ``roam_coupling``,
``roam_fn_coupling``, ``roam_graph_diff``, ``roam_graph_stats``,
``roam_entry_points``, ``roam_patterns``, ``roam_cut``, ``roam_x_lang``.

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


# The 10 wrappers shipped by W300.
W300_TOOL_NAMES: tuple[str, ...] = (
    "roam_clusters",
    "roam_layers",
    "roam_coupling",
    "roam_fn_coupling",
    "roam_graph_diff",
    "roam_graph_stats",
    "roam_entry_points",
    "roam_patterns",
    "roam_cut",
    "roam_x_lang",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W300 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W300_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W300 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W300_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 10 wrappers are read-only (read_only=True implied default)."""
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the architecture cluster "
            f"only contains pure-query commands."
        )

    @pytest.mark.parametrize("tool_name", W300_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 10 wrappers."""

    @pytest.mark.parametrize("tool_name", W300_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W300 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- the "
            f"architecture cluster reads the indexed symbol graph."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W300_TOOL_NAMES)
    def test_wrapper_description_carries_cold_start_hint(
        self, tool_name: str
    ) -> None:
        """W296 hint is auto-appended to every index-gated wrapper."""
        from roam.mcp_extras.preflight import INDEX_REQUIRED_HINT
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert INDEX_REQUIRED_HINT in desc, (
            f"{tool_name} description must end with the W296 hint "
            f"{INDEX_REQUIRED_HINT!r}; actual description: {desc!r}"
        )


# ---------------------------------------------------------------------------
# Per-wrapper CLI argument shape
# ---------------------------------------------------------------------------


class TestRoamClustersArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_clusters

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_clusters()
            mock.assert_called_once_with(["clusters", "--min-size", "3"], ".")

    def test_min_size_and_flags(self) -> None:
        from roam.mcp_server import roam_clusters

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_clusters(min_size=10, mermaid=True, weak=True)
            args = mock.call_args[0][0]
            assert args[:3] == ["clusters", "--min-size", "10"]
            assert "--mermaid" in args
            assert "--weak" in args


class TestRoamLayersArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_layers

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_layers()
            mock.assert_called_once_with(["layers"], ".")

    def test_mermaid_flag(self) -> None:
        from roam.mcp_server import roam_layers

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_layers(mermaid=True)
            args = mock.call_args[0][0]
            assert args[0] == "layers"
            assert "--mermaid" in args


class TestRoamCouplingArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_coupling

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_coupling()
            # No -n (auto-scale), no --staged, no --against
            mock.assert_called_once_with(["coupling"], ".")

    def test_against_with_thresholds(self) -> None:
        from roam.mcp_server import roam_coupling

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_coupling(
                count=30,
                against="HEAD~5..HEAD",
                min_strength=0.5,
                min_cochanges=3,
            )
            args = mock.call_args[0][0]
            assert args[0] == "coupling"
            assert "-n" in args and "30" in args
            assert "--against" in args and "HEAD~5..HEAD" in args
            assert "--min-strength" in args and "0.5" in args
            assert "--min-cochanges" in args and "3" in args


class TestRoamFnCouplingArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_fn_coupling

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fn_coupling()
            mock.assert_called_once_with(
                ["fn-coupling", "--min-count", "3", "-n", "20"], "."
            )

    def test_full_flags(self) -> None:
        from roam.mcp_server import roam_fn_coupling

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fn_coupling(
                min_count=5,
                limit=50,
                include_connected=True,
                include_tests=True,
                since="HEAD~10",
            )
            args = mock.call_args[0][0]
            assert args[:5] == ["fn-coupling", "--min-count", "5", "-n", "50"]
            assert "--include-connected" in args
            assert "--include-tests" in args
            assert "--since" in args and "HEAD~10" in args


class TestRoamGraphDiffArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_graph_diff

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_graph_diff()
            mock.assert_called_once_with(["graph-diff"], ".")

    def test_save_snapshot(self) -> None:
        from roam.mcp_server import roam_graph_diff

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_graph_diff(save_snapshot="pre-refactor")
            args = mock.call_args[0][0]
            assert args[0] == "graph-diff"
            assert "--save-snapshot" in args and "pre-refactor" in args

    def test_base_head_top(self) -> None:
        from roam.mcp_server import roam_graph_diff

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_graph_diff(base="pre", head="post", top=50)
            args = mock.call_args[0][0]
            assert "--base" in args and "pre" in args
            assert "--head" in args and "post" in args
            assert "--top" in args and "50" in args


class TestRoamGraphStatsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_graph_stats

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_graph_stats()
            # Default symbol scope: no --scope flag (LAW 11 - mirror CLI default)
            mock.assert_called_once_with(["graph-stats"], ".")

    def test_file_scope(self) -> None:
        from roam.mcp_server import roam_graph_stats

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_graph_stats(scope="file")
            args = mock.call_args[0][0]
            assert args[0] == "graph-stats"
            assert "--scope" in args and "file" in args


class TestRoamEntryPointsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_entry_points

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_entry_points()
            mock.assert_called_once_with(
                ["entry-points", "--limit", "50"], "."
            )

    def test_protocol_filter(self) -> None:
        from roam.mcp_server import roam_entry_points

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_entry_points(protocol="HTTP", limit=100)
            args = mock.call_args[0][0]
            assert args[:3] == ["entry-points", "--limit", "100"]
            assert "--protocol" in args and "HTTP" in args


class TestRoamPatternsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_patterns

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_patterns()
            mock.assert_called_once_with(["patterns"], ".")

    def test_pattern_filter_and_strict(self) -> None:
        from roam.mcp_server import roam_patterns

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_patterns(pattern="singleton", strict_factory=True)
            args = mock.call_args[0][0]
            assert args[0] == "patterns"
            assert "--pattern" in args and "singleton" in args
            assert "--strict-factory" in args


class TestRoamCutArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_cut

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_cut()
            mock.assert_called_once_with(["cut", "--top", "10"], ".")

    def test_between_with_leak_edges(self) -> None:
        from roam.mcp_server import roam_cut

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_cut(between="auth,payments", leak_edges=True, top=20)
            args = mock.call_args[0][0]
            assert args[:3] == ["cut", "--top", "20"]
            assert "--between" in args
            assert "auth" in args and "payments" in args
            assert "--leak-edges" in args


class TestRoamXLangArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_x_lang

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_x_lang()
            mock.assert_called_once_with(["x-lang"], ".")

    def test_scope(self) -> None:
        from roam.mcp_server import roam_x_lang

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_x_lang(scope="src/")
            args = mock.call_args[0][0]
            assert args[0] == "x-lang"
            assert "--scope" in args and "src/" in args
