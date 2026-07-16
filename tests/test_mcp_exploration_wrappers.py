"""W299 -- MCP exploration & search wrapper tests.

Wave29 sub-wave 1 adds 9 wrappers in ``src/roam/mcp_server.py`` for the
exploration cluster: ``roam_grep``, ``roam_history_grep``,
``roam_refs_text``, ``roam_fan``, ``roam_module``, ``roam_metrics``,
``roam_findings_list``, ``roam_findings_show``, ``roam_findings_count``.

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


# The 9 wrappers shipped by W299.
W299_TOOL_NAMES: tuple[str, ...] = (
    "roam_grep",
    "roam_history_grep",
    "roam_refs_text",
    "roam_fan",
    "roam_module",
    "roam_metrics",
    "roam_findings_list",
    "roam_findings_show",
    "roam_findings_count",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W299 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W299_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W299 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W299_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 9 wrappers are read-only (read_only=True implied default)."""
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the exploration cluster only contains pure-query commands."
        )

    @pytest.mark.parametrize("tool_name", W299_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 9 wrappers."""

    @pytest.mark.parametrize("tool_name", W299_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W299 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- the exploration cluster reads the indexed symbol graph."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W299_TOOL_NAMES)
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


class TestRoamGrepArgShape:
    """``roam_grep`` translates kwargs into the right CLI flags."""

    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_grep(pattern="TODO")
            mock.assert_called_once_with(["grep", "TODO"], ".")

    def test_multi_pattern_with_globs(self) -> None:
        from roam.mcp_server import roam_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_grep(patterns="foo,bar", globs="py,md")
            args, _root = mock.call_args[0]
            assert args[0] == "grep"
            assert "-e" in args and "foo" in args and "bar" in args
            assert "-g" in args and "py" in args and "md" in args

    def test_reachability_flags(self) -> None:
        from roam.mcp_server import roam_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_grep(
                pattern="auth",
                reachable_from="main",
                rank_by="importance",
                group_by="symbol",
            )
            args = mock.call_args[0][0]
            assert "--reachable-from" in args and "main" in args
            assert "--rank-by" in args and "importance" in args
            assert "--group-by" in args and "symbol" in args

    def test_context_packet_flags(self) -> None:
        from roam.mcp_server import roam_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_grep(
                pattern="auth",
                context_lines=5,
                whole_symbol=True,
                max_packets=3,
                max_packet_lines=80,
            )
            args = mock.call_args[0][0]
            assert "--context" in args and "5" in args
            assert "--whole-symbol" in args
            assert "--max-packets" in args and "3" in args
            assert "--max-packet-lines" in args and "80" in args


class TestRoamHistoryGrepArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_history_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_history_grep(pattern="DATABASE_URL")
            mock.assert_called_once_with(["history-grep", "DATABASE_URL"], ".")

    def test_since_and_polarity(self) -> None:
        from roam.mcp_server import roam_history_grep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_history_grep(pattern="deprecated", since="2024-01-01", polarity=True)
            args = mock.call_args[0][0]
            assert "--since" in args and "2024-01-01" in args
            assert "--polarity" in args


class TestRoamRefsTextArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_refs_text

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_refs_text(strings="DATABASE_URL")
            mock.assert_called_once_with(["refs-text", "DATABASE_URL"], ".")

    def test_multiple_strings_with_reachable_from(self) -> None:
        from roam.mcp_server import roam_refs_text

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_refs_text(
                strings="foo.html,bar.html",
                reachable_from="main",
                per_match_detail=True,
            )
            args = mock.call_args[0][0]
            assert args[0] == "refs-text"
            assert "foo.html" in args and "bar.html" in args
            assert "--reachable-from" in args and "main" in args
            assert "--per-match-detail" in args


class TestRoamFanArgShape:
    def test_default_symbol_mode(self) -> None:
        from roam.mcp_server import roam_fan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fan()
            mock.assert_called_once_with(["fan", "symbol", "-n", "20"], ".")

    def test_file_mode_with_persist(self) -> None:
        from roam.mcp_server import roam_fan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fan(mode="file", count=50, no_framework=True, persist=True)
            args = mock.call_args[0][0]
            assert args[0:4] == ["fan", "file", "-n", "50"]
            assert "--no-framework" in args
            assert "--persist" in args


class TestRoamModuleArgShape:
    def test_path_passes_through(self) -> None:
        from roam.mcp_server import roam_module

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_module(path="src/roam/db")
            mock.assert_called_once_with(["module", "src/roam/db"], ".")


class TestRoamMetricsArgShape:
    def test_symbol_passes_through(self) -> None:
        from roam.mcp_server import roam_metrics

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_metrics(symbol="create_user")
            mock.assert_called_once_with(["metrics", "create_user"], ".")


class TestRoamFindingsListArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_findings_list

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_findings_list()
            mock.assert_called_once_with(["findings", "list", "--limit", "100"], ".")

    def test_filtered_by_detector(self) -> None:
        from roam.mcp_server import roam_findings_list

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_findings_list(detector="clones", limit=500)
            args = mock.call_args[0][0]
            assert args[:2] == ["findings", "list"]
            assert "--detector" in args and "clones" in args
            assert "--limit" in args and "500" in args


class TestRoamFindingsShowArgShape:
    def test_id_passes_through(self) -> None:
        from roam.mcp_server import roam_findings_show

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_findings_show(finding_id_str="clones:sym:abcd")
            mock.assert_called_once_with(["findings", "show", "clones:sym:abcd"], ".")


class TestRoamFindingsCountArgShape:
    def test_no_args(self) -> None:
        from roam.mcp_server import roam_findings_count

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_findings_count()
            mock.assert_called_once_with(["findings", "count"], ".")
