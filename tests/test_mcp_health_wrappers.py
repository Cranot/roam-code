"""W301 -- MCP health / quality wrapper tests.

Wave29 sub-wave 3 adds 10 wrappers in ``src/roam/mcp_server.py`` for the
health / quality cluster: ``roam_smells``, ``roam_hotspots``,
``roam_bus_factor``, ``roam_fitness``, ``roam_orphan_imports``,
``roam_eval_retrieve``, ``roam_why_slow``, ``roam_doc_staleness``,
``roam_owner``, ``roam_congestion``.

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
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")
    yield


# The 10 wrappers shipped by W301.
W301_TOOL_NAMES: tuple[str, ...] = (
    "roam_smells",
    "roam_hotspots",
    "roam_bus_factor",
    "roam_fitness",
    "roam_orphan_imports",
    "roam_eval_retrieve",
    "roam_why_slow",
    "roam_doc_staleness",
    "roam_owner",
    "roam_congestion",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W301 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W301_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W301 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W301_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 10 wrappers are read-only (read_only=True implied default)."""
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the health cluster only "
            f"contains pure-query detectors (the persist side-effect "
            f"flags are intentionally NOT surfaced through MCP)."
        )

    @pytest.mark.parametrize("tool_name", W301_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 10 wrappers."""

    @pytest.mark.parametrize("tool_name", W301_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W301 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- every "
            f"detector in the health cluster reads the indexed graph "
            f"or git stats joined against indexed files."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W301_TOOL_NAMES)
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


class TestRoamSmellsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_smells

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_smells()
            mock.assert_called_once_with(["smells"], ".")

    def test_path_filter(self) -> None:
        from roam.mcp_server import roam_smells

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_smells(path="src/roam/cli.py")
            args = mock.call_args[0][0]
            assert args[0] == "smells"
            assert "--file" in args and "src/roam/cli.py" in args

    def test_min_severity_and_tooling(self) -> None:
        from roam.mcp_server import roam_smells

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_smells(min_severity="critical", include_tooling=True)
            args = mock.call_args[0][0]
            assert args[0] == "smells"
            assert "--min-severity" in args and "critical" in args
            assert "--include-tooling" in args


class TestRoamHotspotsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_hotspots

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_hotspots()
            mock.assert_called_once_with(["hotspots"], ".")

    def test_runtime_and_discrepancy(self) -> None:
        from roam.mcp_server import roam_hotspots

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_hotspots(runtime=True, discrepancy=True)
            args = mock.call_args[0][0]
            assert args[0] == "hotspots"
            assert "--runtime" in args
            assert "--discrepancy" in args

    def test_security_mode(self) -> None:
        from roam.mcp_server import roam_hotspots

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_hotspots(security=True, danger=True)
            args = mock.call_args[0][0]
            assert "--security" in args
            assert "--danger" in args


class TestRoamBusFactorArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_bus_factor

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_bus_factor()
            mock.assert_called_once_with(["bus-factor", "--limit", "20", "--stale-months", "6"], ".")

    def test_brain_methods_and_team_mode(self) -> None:
        from roam.mcp_server import roam_bus_factor

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_bus_factor(
                limit=50,
                stale_months=12,
                brain_methods=True,
                force_team_mode=True,
            )
            args = mock.call_args[0][0]
            assert args[:5] == [
                "bus-factor",
                "--limit",
                "50",
                "--stale-months",
                "12",
            ]
            assert "--brain-methods" in args
            assert "--force-team-mode" in args


class TestRoamFitnessArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_fitness

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fitness()
            mock.assert_called_once_with(["fitness"], ".")

    def test_rule_filter_and_explain(self) -> None:
        from roam.mcp_server import roam_fitness

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fitness(rule="no-cycles", explain=True)
            args = mock.call_args[0][0]
            assert args[0] == "fitness"
            assert "--rule" in args and "no-cycles" in args
            assert "--explain" in args

    def test_input_path_baseline(self) -> None:
        """W332 canonical ``input_path`` -- baseline sidecar."""
        from roam.mcp_server import roam_fitness

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_fitness(input_path=".roam/fitness-baseline.json")
            args = mock.call_args[0][0]
            assert args[0] == "fitness"
            assert "--baseline" in args
            assert ".roam/fitness-baseline.json" in args


class TestRoamOrphanImportsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_orphan_imports

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_orphan_imports()
            # Default lang="all" -- mirror CLI default (LAW 11)
            mock.assert_called_once_with(["orphan-imports"], ".")

    def test_lang_filter(self) -> None:
        from roam.mcp_server import roam_orphan_imports

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_orphan_imports(lang="python")
            args = mock.call_args[0][0]
            assert args[0] == "orphan-imports"
            assert "--lang" in args and "python" in args


class TestRoamEvalRetrieveArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_eval_retrieve

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_eval_retrieve()
            mock.assert_called_once_with(["eval-retrieve", "--rerank", "fast"], ".")

    def test_input_path_tasks(self) -> None:
        """W332 canonical ``input_path`` -- tasks JSONL."""
        from roam.mcp_server import roam_eval_retrieve

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_eval_retrieve(input_path="bench/retrieve/custom.jsonl")
            args = mock.call_args[0][0]
            assert args[0] == "eval-retrieve"
            assert "--tasks" in args
            assert "bench/retrieve/custom.jsonl" in args

    def test_sweep_and_gate(self) -> None:
        from roam.mcp_server import roam_eval_retrieve

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_eval_retrieve(
                rerank="off",
                sweep=True,
                min_recall_at_20=0.6,
                quick=True,
            )
            args = mock.call_args[0][0]
            assert args[0] == "eval-retrieve"
            assert "--rerank" in args and "off" in args
            assert "--sweep" in args
            assert "--min-recall-at-20" in args and "0.6" in args
            assert "--quick" in args


class TestRoamWhySlowArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_why_slow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_why_slow()
            mock.assert_called_once_with(["why-slow", "--top", "20"], ".")

    def test_changed_against_base(self) -> None:
        from roam.mcp_server import roam_why_slow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_why_slow(top=50, changed=True, base="main", min_calls=10)
            args = mock.call_args[0][0]
            assert args[:3] == ["why-slow", "--top", "50"]
            assert "--changed" in args
            assert "--base" in args and "main" in args
            assert "--min-calls" in args and "10" in args


class TestRoamDocStalenessArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_doc_staleness

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_doc_staleness()
            mock.assert_called_once_with(["doc-staleness", "--limit", "20", "--days", "90"], ".")

    def test_prose_drift(self) -> None:
        from roam.mcp_server import roam_doc_staleness

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_doc_staleness(limit=50, days=30, include_prose_drift=True)
            args = mock.call_args[0][0]
            assert args[:5] == [
                "doc-staleness",
                "--limit",
                "50",
                "--days",
                "30",
            ]
            assert "--include-prose-drift" in args


class TestRoamOwnerArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_owner

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_owner("src/roam/cli.py")
            mock.assert_called_once_with(["owner", "src/roam/cli.py"], ".")

    def test_directory_path(self) -> None:
        from roam.mcp_server import roam_owner

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_owner(path="src/roam/db/")
            args = mock.call_args[0][0]
            assert args == ["owner", "src/roam/db/"]


class TestRoamCongestionArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_congestion

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_congestion()
            mock.assert_called_once_with(
                [
                    "congestion",
                    "--window",
                    "90",
                    "--min-authors",
                    "3",
                    "--limit",
                    "30",
                ],
                ".",
            )

    def test_custom_window(self) -> None:
        from roam.mcp_server import roam_congestion

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_congestion(window=30, min_authors=5, limit=10)
            args = mock.call_args[0][0]
            assert args == [
                "congestion",
                "--window",
                "30",
                "--min-authors",
                "5",
                "--limit",
                "10",
            ]
