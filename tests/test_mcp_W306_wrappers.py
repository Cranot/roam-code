"""W306 -- MCP getting-started / refactoring / workflow / compliance wrapper tests.

Wave29 sub-wave 8 adds 13 wrappers in ``src/roam/mcp_server.py`` for the
heterogeneous tail of the MCP wrapper backfill:

* getting-started / overview: ``roam_describe``, ``roam_map``,
  ``roam_minimap``, ``roam_workflow``
* refactoring / contracts:    ``roam_intent``, ``roam_invariants``,
  ``roam_why``
* discovery / metrics:        ``roam_adrs``, ``roam_architecture_drift``,
  ``roam_dogfood_aggregate``
* compliance:                 ``roam_article_12_check``
* review / replay:            ``roam_postmortem``, ``roam_pr_prep``

This module pins:

* each wrapper is registered in ``_TOOL_METADATA`` under the expected name
* each wrapper IS NOT in ``_NO_INDEX_NEEDED`` (they all require an index)
* each wrapper's description carries the W296 ``INDEX_REQUIRED_HINT``
  (auto-appended by ``maybe_decorate_description``)
* each wrapper's CLI argument shape is what the underlying command
  expects (verified by mocking ``_run_roam`` and asserting the args).

Wrappers that have CLI flags WHICH MUTATE DISK (``describe --write`` /
``minimap --update`` / ``minimap --init-notes``) intentionally DO NOT
expose those flags through MCP. The wrappers stay read-only by
construction so the MCP surface stays read-only by construction.
``pr-bundle`` / ``fleet`` (state-mutating commands with multi-subcommand
shapes) defer to W307.
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


# The 13 wrappers shipped by W306.
W306_TOOL_NAMES: tuple[str, ...] = (
    "roam_adrs",
    "roam_architecture_drift",
    "roam_article_12_check",
    "roam_describe",
    "roam_dogfood_aggregate",
    "roam_intent",
    "roam_invariants",
    "roam_map",
    "roam_minimap",
    "roam_postmortem",
    "roam_pr_prep",
    "roam_why",
    "roam_workflow",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W306 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W306_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W306 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W306_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 13 wrappers are read-only at the MCP surface.

        ``describe`` and ``minimap`` have CLI flags that write to disk
        (``--write`` / ``--update`` / ``--init-notes``) but the wrappers
        intentionally DO NOT expose those flags so the MCP surface stays
        read-only by construction. ``pr-bundle`` / ``fleet`` (genuinely
        state-mutating commands) defer to W307.
        """
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the W306 cluster only "
            f"exposes the non-mutating CLI paths; state-mutating "
            f"commands (pr-bundle, fleet) defer to W307."
        )

    @pytest.mark.parametrize("tool_name", W306_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 13 wrappers."""

    @pytest.mark.parametrize("tool_name", W306_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W306 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED. The W306 "
            f"cluster reads the indexed symbol graph for: top-symbol "
            f"PageRank (describe/map/minimap), call-graph reach "
            f"(why/invariants), doc-to-symbol linking (intent/adrs), "
            f"and git history replay against indexed file rows "
            f"(postmortem/pr-prep)."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W306_TOOL_NAMES)
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


class TestRoamAdrsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_adrs

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_adrs()
            # Default limit=50 mirrors the CLI default per LAW 11.
            mock.assert_called_once_with(["adrs", "--limit", "50"], ".")

    def test_filter_status(self) -> None:
        from roam.mcp_server import roam_adrs

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_adrs(filter_status="accepted", limit=10)
            args = mock.call_args[0][0]
            assert args[0] == "adrs"
            assert "--limit" in args and "10" in args
            assert "--status" in args and "accepted" in args


class TestRoamArchitectureDriftArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_architecture_drift

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults window="30d" + top=10 mirror the CLI per LAW 11.
            roam_architecture_drift()
            mock.assert_called_once_with(
                ["architecture-drift", "--window", "30d", "--top", "10"],
                ".",
            )

    def test_window_override(self) -> None:
        from roam.mcp_server import roam_architecture_drift

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_architecture_drift(window="90d", top=25)
            args = mock.call_args[0][0]
            assert args[0] == "architecture-drift"
            assert "--window" in args and "90d" in args
            assert "--top" in args and "25" in args


class TestRoamArticle12CheckArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_article_12_check

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_article_12_check()
            mock.assert_called_once_with(["article-12-check"], ".")


class TestRoamDescribeArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_describe

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_describe()
            mock.assert_called_once_with(["describe"], ".")

    def test_agent_prompt(self) -> None:
        from roam.mcp_server import roam_describe

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_describe(agent_prompt=True)
            args = mock.call_args[0][0]
            assert args == ["describe", "--agent-prompt"]


class TestRoamDogfoodAggregateArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_dogfood_aggregate

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults top=10 + limit=50 mirror the CLI per LAW 11.
            roam_dogfood_aggregate()
            mock.assert_called_once_with(["dogfood-aggregate", "--top", "10", "--limit", "50"], ".")

    def test_filters(self) -> None:
        from roam.mcp_server import roam_dogfood_aggregate

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_dogfood_aggregate(
                path="internal/dogfood/evals/",
                show_all=True,
                severity="H",
                finding_type="wrong",
                since="2026-01-01",
                top=5,
                limit=20,
            )
            args = mock.call_args[0][0]
            assert args[0] == "dogfood-aggregate"
            assert "--path" in args and "internal/dogfood/evals/" in args
            assert "--all" in args
            assert "--severity" in args and "H" in args
            assert "--type" in args and "wrong" in args
            assert "--since" in args and "2026-01-01" in args
            assert "--top" in args and "5" in args
            assert "--limit" in args and "20" in args


class TestRoamIntentArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_intent

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default top=20 mirrors the CLI default per LAW 11.
            roam_intent()
            mock.assert_called_once_with(["intent", "--top", "20"], ".")

    def test_symbol(self) -> None:
        from roam.mcp_server import roam_intent

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_intent(symbol="build_query")
            args = mock.call_args[0][0]
            assert args[0] == "intent"
            assert "--symbol" in args and "build_query" in args

    def test_path(self) -> None:
        from roam.mcp_server import roam_intent

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_intent(path="docs/architecture.md")
            args = mock.call_args[0][0]
            assert args[0] == "intent"
            assert "--doc" in args and "docs/architecture.md" in args

    def test_flags(self) -> None:
        from roam.mcp_server import roam_intent

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_intent(drift=True, undocumented=True, top=50)
            args = mock.call_args[0][0]
            assert "--drift" in args
            assert "--undocumented" in args
            assert "--top" in args and "50" in args


class TestRoamInvariantsArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_invariants

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default top=20 mirrors the CLI default per LAW 11.
            roam_invariants()
            mock.assert_called_once_with(["invariants", "--top", "20"], ".")

    def test_symbol(self) -> None:
        from roam.mcp_server import roam_invariants

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_invariants(symbol="parse_amount")
            args = mock.call_args[0][0]
            assert args[0] == "invariants"
            # symbol arrives as the positional after the flags.
            assert "parse_amount" in args

    def test_public_api_breaking_risk(self) -> None:
        from roam.mcp_server import roam_invariants

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_invariants(public_api=True, breaking_risk=True, top=10)
            args = mock.call_args[0][0]
            assert "--public-api" in args
            assert "--breaking-risk" in args
            assert "--top" in args and "10" in args


class TestRoamMapArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_map

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default count=20 mirrors the CLI default per LAW 11.
            roam_map()
            mock.assert_called_once_with(["map", "-n", "20"], ".")

    def test_full_and_budget(self) -> None:
        from roam.mcp_server import roam_map

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_map(count=50, full=True, budget=2000)
            args = mock.call_args[0][0]
            assert args[0] == "map"
            assert "-n" in args and "50" in args
            assert "--full" in args
            assert "--budget" in args and "2000" in args

    def test_seed_path(self) -> None:
        from roam.mcp_server import roam_map

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_map(path="src/roam/cli.py", depth=3)
            args = mock.call_args[0][0]
            assert "--seed" in args and "src/roam/cli.py" in args
            assert "--depth" in args and "3" in args


class TestRoamMinimapArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_minimap

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_minimap()
            mock.assert_called_once_with(["minimap"], ".")


class TestRoamPostmortemArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_postmortem

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults limit=100 + show=10 mirror the CLI per LAW 11.
            roam_postmortem(commit_range="HEAD~30..HEAD")
            mock.assert_called_once_with(
                [
                    "postmortem",
                    "HEAD~30..HEAD",
                    "--limit",
                    "100",
                    "--show",
                    "10",
                ],
                ".",
            )

    def test_overrides(self) -> None:
        from roam.mcp_server import roam_postmortem

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_postmortem(commit_range="v12.30..HEAD", limit=50, show=25)
            args = mock.call_args[0][0]
            assert args[:2] == ["postmortem", "v12.30..HEAD"]
            assert "--limit" in args and "50" in args
            assert "--show" in args and "25" in args


class TestRoamPrPrepArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_pr_prep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default high_callers=10 mirrors the CLI per LAW 11.
            roam_pr_prep()
            mock.assert_called_once_with(["pr-prep", "--high-callers", "10"], ".")

    def test_commit_range(self) -> None:
        from roam.mcp_server import roam_pr_prep

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_pr_prep(commit_range="main..HEAD", high_callers=20)
            args = mock.call_args[0][0]
            assert args[0] == "pr-prep"
            assert "--high-callers" in args and "20" in args
            assert "main..HEAD" in args


class TestRoamWhyArgShape:
    def test_single_symbol(self) -> None:
        from roam.mcp_server import roam_why

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_why(symbols=("parseAmount",))
            mock.assert_called_once_with(["why", "parseAmount"], ".")

    def test_batch_symbols(self) -> None:
        from roam.mcp_server import roam_why

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_why(symbols=("parseAmount", "formatNumber", "clearGrid"))
            args = mock.call_args[0][0]
            assert args == [
                "why",
                "parseAmount",
                "formatNumber",
                "clearGrid",
            ]


class TestRoamWorkflowArgShape:
    def test_default_invocation_lists(self) -> None:
        from roam.mcp_server import roam_workflow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default (no recipe, no flags) still calls workflow -- the CLI
            # default behaviour is to list recipes when no name is given.
            roam_workflow()
            mock.assert_called_once_with(["workflow"], ".")

    def test_list_recipes(self) -> None:
        from roam.mcp_server import roam_workflow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_workflow(list_recipes=True)
            args = mock.call_args[0][0]
            assert "--list" in args

    def test_recipe_name(self) -> None:
        from roam.mcp_server import roam_workflow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_workflow(recipe_name="first-contact")
            args = mock.call_args[0][0]
            assert args[0] == "workflow"
            assert "first-contact" in args

    def test_query_and_next_after(self) -> None:
        from roam.mcp_server import roam_workflow

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_workflow(query="handleSave", next_after="impact")
            args = mock.call_args[0][0]
            assert args[0] == "workflow"
            assert "--query" in args and "handleSave" in args
            assert "--next" in args and "impact" in args
