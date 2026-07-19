"""W302 -- MCP refactoring / test-surface wrapper tests.

Wave29 sub-wave 4 adds 9 wrappers in ``src/roam/mcp_server.py`` for the
refactoring cluster: ``roam_safe_delete``, ``roam_safe_zones``,
``roam_delete_check``, ``roam_flag_dead``, ``roam_sketch``,
``roam_split``, ``roam_test_map``, ``roam_test_pyramid``,
``roam_test_scaffold``.

This module pins:

* each wrapper is registered in ``_TOOL_METADATA`` under the expected name
* each wrapper IS NOT in ``_NO_INDEX_NEEDED`` (they all require an index)
* each wrapper's description carries the W296 ``INDEX_REQUIRED_HINT``
  (auto-appended by ``maybe_decorate_description``)
* each wrapper's CLI argument shape is what the underlying command
  expects (verified by mocking ``_run_roam`` and asserting the args).
* option-dependent writers declare their maximum callable effects even when
  their default invocation is a dry run.
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
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")
    yield


# The 9 wrappers shipped by W302.
W302_TOOL_NAMES: tuple[str, ...] = (
    "roam_safe_delete",
    "roam_safe_zones",
    "roam_delete_check",
    "roam_flag_dead",
    "roam_sketch",
    "roam_split",
    "roam_test_map",
    "roam_test_pyramid",
    "roam_test_scaffold",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W302 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W302_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W302 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W302_TOOL_NAMES[:-1])
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """The eight analysis-only wrappers are read-only.

        Even ``roam_safe_delete`` and ``roam_delete_check`` -- which
        carry "delete" in their name -- only REPORT verdicts; the
        underlying CLI never touches the filesystem.
        """
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the refactoring cluster "
            f"only contains analyses and gates; any disk-writing side "
            f"effect is opt-in via a caller-supplied flag."
        )

    def test_test_scaffold_declares_maximum_callable_effects(self) -> None:
        """``write=True`` makes the tool a conservative static writer."""
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA["roam_test_scaffold"]
        assert meta["read_only"] is False
        assert meta["destructive"] is False
        assert meta["idempotent"] is False

    @pytest.mark.parametrize("tool_name", W302_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 9 wrappers."""

    @pytest.mark.parametrize("tool_name", W302_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W302 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- every "
            f"command in the refactoring cluster reads the indexed "
            f"graph (call edges, file edges, PageRank, file roles, "
            f"or the symbols table)."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W302_TOOL_NAMES)
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


class TestRoamSafeDeleteArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_safe_delete

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_safe_delete("handleSave")
            mock.assert_called_once_with(["safe-delete", "handleSave"], ".")

    def test_root_override(self) -> None:
        from roam.mcp_server import roam_safe_delete

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_safe_delete(symbol="MyClass.method", root="/repo/x")
            mock.assert_called_once_with(["safe-delete", "MyClass.method"], "/repo/x")


class TestRoamSafeZonesArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_safe_zones

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default depth=5 mirrors the CLI default per LAW 11.
            roam_safe_zones("src/roam/cli.py")
            mock.assert_called_once_with(["safe-zones", "src/roam/cli.py", "--depth", "5"], ".")

    def test_custom_depth(self) -> None:
        from roam.mcp_server import roam_safe_zones

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_safe_zones(symbol="handleSave", depth=8)
            args = mock.call_args[0][0]
            assert args[:2] == ["safe-zones", "handleSave"]
            assert "--depth" in args and "8" in args


class TestRoamDeleteCheckArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_delete_check

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_delete_check()
            mock.assert_called_once_with(
                [
                    "delete-check",
                    "--source",
                    "working",
                    "--base-ref",
                    "main",
                    "-n",
                    "20",
                ],
                ".",
            )

    def test_pr_source_with_ci(self) -> None:
        from roam.mcp_server import roam_delete_check

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_delete_check(source="pr", base_ref="develop", ci=True)
            args = mock.call_args[0][0]
            assert args[0] == "delete-check"
            assert "--source" in args and "pr" in args
            assert "--base-ref" in args and "develop" in args
            assert "--ci" in args

    def test_commit_range_and_reachable_from(self) -> None:
        from roam.mcp_server import roam_delete_check

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_delete_check(
                commit_range="HEAD~3..HEAD",
                reachable_from="main",
                include_line_deletions=True,
                count=50,
            )
            args = mock.call_args[0][0]
            assert "--commit-range" in args and "HEAD~3..HEAD" in args
            assert "--reachable-from" in args and "main" in args
            assert "--include-line-deletions" in args
            assert "-n" in args and "50" in args


class TestRoamFlagDeadArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_flag_dead

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_flag_dead()
            mock.assert_called_once_with(["flag-dead"], ".")

    def test_input_path_config(self) -> None:
        """W332 canonical ``input_path`` -- known-stale flag list."""
        from roam.mcp_server import roam_flag_dead

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_flag_dead(input_path=".roam/stale-flags.txt")
            args = mock.call_args[0][0]
            assert args[0] == "flag-dead"
            assert "--config" in args
            assert ".roam/stale-flags.txt" in args

    def test_include_tests(self) -> None:
        from roam.mcp_server import roam_flag_dead

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_flag_dead(include_tests=True)
            args = mock.call_args[0][0]
            assert "--include-tests" in args


class TestRoamSketchArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_sketch

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_sketch("src/roam/db")
            mock.assert_called_once_with(["sketch", "src/roam/db"], ".")

    def test_full_flag(self) -> None:
        from roam.mcp_server import roam_sketch

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_sketch(directory="src/roam/db", full=True)
            args = mock.call_args[0][0]
            assert args[:2] == ["sketch", "src/roam/db"]
            assert "--full" in args


class TestRoamSplitArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_split

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default min_group=2 mirrors the CLI default per LAW 11.
            roam_split("src/roam/cli.py")
            mock.assert_called_once_with(["split", "src/roam/cli.py", "--min-group", "2"], ".")

    def test_custom_min_group(self) -> None:
        from roam.mcp_server import roam_split

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_split(path="src/roam/mcp_server.py", min_group=5)
            args = mock.call_args[0][0]
            assert args[:2] == ["split", "src/roam/mcp_server.py"]
            assert "--min-group" in args and "5" in args


class TestRoamTestMapArgShape:
    def test_default_invocation_symbol(self) -> None:
        from roam.mcp_server import roam_test_map

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_map("handleSave")
            mock.assert_called_once_with(["test-map", "handleSave"], ".")

    def test_default_invocation_file_path(self) -> None:
        from roam.mcp_server import roam_test_map

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_map(symbol="src/roam/cli.py")
            mock.assert_called_once_with(["test-map", "src/roam/cli.py"], ".")


class TestRoamTestPyramidArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_test_pyramid

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_pyramid()
            mock.assert_called_once_with(["test-pyramid"], ".")

    def test_root_override(self) -> None:
        from roam.mcp_server import roam_test_pyramid

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_pyramid(root="/repo/x")
            mock.assert_called_once_with(["test-pyramid"], "/repo/x")


class TestRoamTestScaffoldArgShape:
    def test_default_invocation_dry_run(self) -> None:
        from roam.mcp_server import roam_test_scaffold

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default write=False keeps dry-run per LAW 11.
            roam_test_scaffold("src/roam/cli.py")
            mock.assert_called_once_with(["test-scaffold", "src/roam/cli.py"], ".")

    def test_write_flag(self) -> None:
        from roam.mcp_server import roam_test_scaffold

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_scaffold("MyClass", write=True)
            args = mock.call_args[0][0]
            assert args[:2] == ["test-scaffold", "MyClass"]
            assert "--write" in args

    def test_framework_override(self) -> None:
        from roam.mcp_server import roam_test_scaffold

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_test_scaffold(symbol="src/utils.py", framework="unittest")
            args = mock.call_args[0][0]
            assert args[:2] == ["test-scaffold", "src/utils.py"]
            assert "--framework" in args and "unittest" in args
