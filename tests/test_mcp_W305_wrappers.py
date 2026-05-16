"""W305 -- MCP reports & audit wrapper tests.

Wave29 sub-wave 7 adds 11 wrappers in ``src/roam/mcp_server.py`` for the
reports & audit cluster: ``roam_audit``, ``roam_report``, ``roam_risk``,
``roam_stats``, ``roam_compare``, ``roam_evidence_diff``, plus 5 oracle
subcommands (``roam_oracle_symbol_exists``, ``roam_oracle_route_exists``,
``roam_oracle_is_test_only``, ``roam_oracle_is_reachable_from_entry``,
``roam_oracle_is_clone_of``).

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


# The 11 wrappers shipped by W305.
W305_TOOL_NAMES: tuple[str, ...] = (
    "roam_audit",
    "roam_report",
    "roam_risk",
    "roam_stats",
    "roam_compare",
    "roam_evidence_diff",
    "roam_oracle_symbol_exists",
    "roam_oracle_route_exists",
    "roam_oracle_is_test_only",
    "roam_oracle_is_reachable_from_entry",
    "roam_oracle_is_clone_of",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W305 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W305_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W305 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W305_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 11 wrappers are read-only.

        ``audit`` / ``report`` / ``risk`` / ``stats`` are composite read
        recipes that emit envelopes only. ``compare`` / ``evidence-diff``
        diff two on-disk artifacts the agent supplies. The 5 oracle
        subcommands answer cheap yes/no queries against the indexed
        graph. None write to disk.
        """
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the reports & audit "
            f"cluster only contains report recipes / cross-index diffs "
            f"/ boolean oracles; no wrapper writes to disk."
        )

    @pytest.mark.parametrize("tool_name", W305_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 11 wrappers."""

    @pytest.mark.parametrize("tool_name", W305_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W305 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- every "
            f"command in the reports & audit cluster reads the indexed "
            f"graph (audit rollups read symbols / edges / metrics; "
            f"compare / evidence-diff read .roam/index.db; oracle "
            f"subcommands read symbols / clones / call edges)."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W305_TOOL_NAMES)
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


class TestRoamAuditArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_audit

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_audit()
            mock.assert_called_once_with(["audit"], ".")

    def test_brief(self) -> None:
        from roam.mcp_server import roam_audit

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_audit(brief=True)
            args = mock.call_args[0][0]
            assert args == ["audit", "--brief"]


class TestRoamReportArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_report

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_report()
            mock.assert_called_once_with(["report"], ".")

    def test_preset_only(self) -> None:
        from roam.mcp_server import roam_report

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_report(preset="pre-pr")
            args = mock.call_args[0][0]
            assert args == ["report", "pre-pr"]

    def test_list_presets(self) -> None:
        from roam.mcp_server import roam_report

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_report(list_presets=True)
            args = mock.call_args[0][0]
            assert "--list" in args

    def test_strict_markdown_config(self) -> None:
        from roam.mcp_server import roam_report

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_report(
                preset="security",
                strict=True,
                markdown=True,
                config_path="custom.json",
            )
            args = mock.call_args[0][0]
            assert args[0] == "report"
            assert "--strict" in args
            assert "--md" in args
            assert "--config" in args and "custom.json" in args
            assert "security" in args


class TestRoamRiskArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_risk

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_risk()
            mock.assert_called_once_with(["risk"], ".")

    def test_top_and_domain(self) -> None:
        from roam.mcp_server import roam_risk

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_risk(top=20, domain="payment,tax")
            args = mock.call_args[0][0]
            assert args[0] == "risk"
            assert "-n" in args and "20" in args
            assert "--domain" in args and "payment,tax" in args

    def test_flags(self) -> None:
        from roam.mcp_server import roam_risk

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_risk(explain=True, include_tests=True, show_suppressed=True)
            args = mock.call_args[0][0]
            assert "--explain" in args
            assert "--include-tests" in args
            assert "--show-suppressed" in args


class TestRoamStatsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_stats

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default days=30 mirrors the CLI default per LAW 11.
            roam_stats()
            mock.assert_called_once_with(["stats", "--days", "30"], ".")

    def test_days_override(self) -> None:
        from roam.mcp_server import roam_stats

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_stats(days=90)
            args = mock.call_args[0][0]
            assert args == ["stats", "--days", "90"]


class TestRoamCompareArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_compare

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults top=15, threshold=5 mirror the CLI per LAW 11.
            roam_compare(
                baseline_path="old/.roam/index.db",
                target_path="new/.roam/index.db",
            )
            mock.assert_called_once_with(
                [
                    "compare",
                    "old/.roam/index.db",
                    "new/.roam/index.db",
                    "--top",
                    "15",
                    "--threshold",
                    "5",
                ],
                ".",
            )

    def test_top_and_threshold_override(self) -> None:
        from roam.mcp_server import roam_compare

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_compare(
                baseline_path="a.db",
                target_path="b.db",
                top=50,
                threshold=20,
            )
            args = mock.call_args[0][0]
            assert args[:3] == ["compare", "a.db", "b.db"]
            assert "--top" in args and "50" in args
            assert "--threshold" in args and "20" in args


class TestRoamEvidenceDiffArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_evidence_diff

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_evidence_diff(
                old_path="evidence/old.json",
                new_path="evidence/new.json",
            )
            mock.assert_called_once_with(
                ["evidence-diff", "evidence/old.json", "evidence/new.json"],
                ".",
            )


class TestRoamOracleSymbolExistsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_oracle_symbol_exists

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_oracle_symbol_exists(symbol="handleSave")
            mock.assert_called_once_with(["oracle", "symbol-exists", "handleSave"], ".")


class TestRoamOracleRouteExistsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_oracle_route_exists

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_oracle_route_exists(route_path="/api/users/:id")
            mock.assert_called_once_with(["oracle", "route-exists", "/api/users/:id"], ".")


class TestRoamOracleIsTestOnlyArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_oracle_is_test_only

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_oracle_is_test_only(symbol="_test_helper")
            mock.assert_called_once_with(["oracle", "is-test-only", "_test_helper"], ".")


class TestRoamOracleIsReachableFromEntryArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_oracle_is_reachable_from_entry

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default max_hops=10 mirrors the CLI default per LAW 11.
            roam_oracle_is_reachable_from_entry(symbol="process_payment")
            mock.assert_called_once_with(
                [
                    "oracle",
                    "is-reachable-from-entry",
                    "process_payment",
                    "--max-hops",
                    "10",
                ],
                ".",
            )

    def test_max_hops_override(self) -> None:
        from roam.mcp_server import roam_oracle_is_reachable_from_entry

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_oracle_is_reachable_from_entry(symbol="login", max_hops=3)
            args = mock.call_args[0][0]
            assert args[:3] == ["oracle", "is-reachable-from-entry", "login"]
            assert "--max-hops" in args and "3" in args


class TestRoamOracleIsCloneOfArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_oracle_is_clone_of

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_oracle_is_clone_of(symbol="validate_input")
            mock.assert_called_once_with(["oracle", "is-clone-of", "validate_input"], ".")
