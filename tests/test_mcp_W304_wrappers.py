"""W304 -- MCP agent-OS daily flow wrapper tests.

Wave29 sub-wave 6 adds 10 wrappers in ``src/roam/mcp_server.py`` for the
agent-OS daily flow cluster: ``roam_brief``, ``roam_next``,
``roam_recommend``, ``roam_plan``, ``roam_agent_plan``,
``roam_agent_context``, ``roam_agent_score``, ``roam_guard``,
``roam_adversarial``, ``roam_migration_plan``.

This module pins:

* each wrapper is registered in ``_TOOL_METADATA`` under the expected name
* each wrapper IS NOT in ``_NO_INDEX_NEEDED`` (they all require an index)
* each wrapper's description carries the W296 ``INDEX_REQUIRED_HINT``
  (auto-appended by ``maybe_decorate_description``)
* each wrapper's CLI argument shape is what the underlying command
  expects (verified by mocking ``_run_roam`` and asserting the args).
"""

from __future__ import annotations

import asyncio
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


# The 10 wrappers shipped by W304.
W304_TOOL_NAMES: tuple[str, ...] = (
    "roam_brief",
    "roam_next",
    "roam_recommend",
    "roam_plan",
    "roam_agent_plan",
    "roam_agent_context",
    "roam_agent_score",
    "roam_guard",
    "roam_adversarial",
    "roam_migration_plan",
)


# ---------------------------------------------------------------------------
# Registry presence + cold-start guard wiring
# ---------------------------------------------------------------------------


class TestRegistryPresence:
    """Each W304 wrapper is registered with the right name + metadata."""

    @pytest.mark.parametrize("tool_name", W304_TOOL_NAMES)
    def test_wrapper_is_registered(self, tool_name: str) -> None:
        """``_TOOL_METADATA`` must carry an entry for each W304 wrapper."""
        from roam.mcp_server import _TOOL_METADATA

        assert tool_name in _TOOL_METADATA, (
            f"{tool_name} not found in _TOOL_METADATA -- the @_tool "
            f"decorator block must have failed to run, or the name "
            f"changed without updating this test."
        )

    @pytest.mark.parametrize("tool_name", W304_TOOL_NAMES)
    def test_wrapper_is_read_only(self, tool_name: str) -> None:
        """All 10 wrappers are read-only.

        The agent-OS daily flow surface is composed of recipes / routers
        / scorers / context packers. None of them mutate disk state --
        ``brief`` / ``next`` / ``recommend`` / ``plan`` / ``agent-plan``
        / ``agent-context`` / ``agent-score`` / ``guard`` /
        ``adversarial`` / ``migration-plan`` all emit envelopes only.
        """
        from roam.mcp_server import _TOOL_METADATA

        meta = _TOOL_METADATA[tool_name]
        assert meta.get("read_only", True) is True, (
            f"{tool_name} must be read-only -- the agent-OS daily flow "
            f"cluster only contains recipes / routers / scorers / "
            f"context packers; no wrapper writes to disk."
        )

    @pytest.mark.parametrize("tool_name", W304_TOOL_NAMES)
    def test_wrapper_has_description(self, tool_name: str) -> None:
        """Each wrapper must carry a non-empty description string."""
        from roam.mcp_server import _TOOL_METADATA

        desc = _TOOL_METADATA[tool_name].get("description", "")
        assert desc, f"{tool_name} has empty description"


class TestColdStartGuardWiring:
    """W296 cold-start guard must apply automatically to all 10 wrappers."""

    @pytest.mark.parametrize("tool_name", W304_TOOL_NAMES)
    def test_wrapper_requires_index(self, tool_name: str) -> None:
        """Each W304 wrapper requires an index -- not in ``_NO_INDEX_NEEDED``."""
        from roam.mcp_extras.preflight import _NO_INDEX_NEEDED, needs_index

        assert tool_name not in _NO_INDEX_NEEDED, (
            f"{tool_name} must NOT be in _NO_INDEX_NEEDED -- every "
            f"command in the agent-OS daily flow cluster reads the "
            f"indexed graph (call edges, symbol bodies, partitions, "
            f"clusters, or the symbols table) either directly or via "
            f"its composed subcommands."
        )
        assert needs_index(tool_name) is True, (
            f"needs_index({tool_name!r}) must return True so the W296 "
            f"cold-start guard short-circuits when .roam/index.db is "
            f"missing."
        )

    @pytest.mark.parametrize("tool_name", W304_TOOL_NAMES)
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


class TestRoamBriefArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_brief

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default top_runs=3 mirrors the CLI default per LAW 11.
            roam_brief()
            mock.assert_called_once_with(["brief", "--top-runs", "3"], ".")

    def test_section_skips(self) -> None:
        from roam.mcp_server import roam_brief

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_brief(
                no_next=True,
                no_pr_bundle=True,
                no_highlights=True,
                no_runs=True,
                no_mode=True,
            )
            args = mock.call_args[0][0]
            assert args[0] == "brief"
            assert "--no-next" in args
            assert "--no-pr-bundle" in args
            assert "--no-highlights" in args
            assert "--no-runs" in args
            assert "--no-mode" in args

    def test_top_runs_override(self) -> None:
        from roam.mcp_server import roam_brief

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_brief(top_runs=10)
            args = mock.call_args[0][0]
            assert "--top-runs" in args and "10" in args


class TestRoamNextArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_next

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_next()
            mock.assert_called_once_with(["next"], ".")


class TestRoamRecommendArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_recommend

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default limit=10 mirrors the CLI default per LAW 11.
            roam_recommend(symbol="handleSave")
            mock.assert_called_once_with(["recommend", "handleSave", "--limit", "10"], ".")

    def test_limit_override(self) -> None:
        from roam.mcp_server import roam_recommend

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_recommend(symbol="MyClass", limit=25)
            args = mock.call_args[0][0]
            assert args[:2] == ["recommend", "MyClass"]
            assert "--limit" in args and "25" in args


class TestRoamPlanArgShape:
    def test_default_invocation_with_symbol(self) -> None:
        from roam.mcp_server import roam_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults task='refactor', depth=2 mirror CLI per LAW 11.
            roam_plan(symbol="login_user")
            args = mock.call_args[0][0]
            assert args[0] == "plan"
            assert "--task" in args and "refactor" in args
            assert "--depth" in args and "2" in args
            assert "login_user" in args

    def test_task_and_depth_override(self) -> None:
        from roam.mcp_server import roam_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_plan(symbol="parse_amount", task="debug", depth=3)
            args = mock.call_args[0][0]
            assert "--task" in args and "debug" in args
            assert "--depth" in args and "3" in args
            assert "parse_amount" in args

    def test_file_path(self) -> None:
        from roam.mcp_server import roam_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_plan(file_path="src/api.py", task="review")
            args = mock.call_args[0][0]
            assert args[0] == "plan"
            # W1099: CLI flag renamed --file -> --path (alias preserved)
            assert "--path" in args and "src/api.py" in args
            assert "--task" in args and "review" in args

    def test_staged(self) -> None:
        from roam.mcp_server import roam_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_plan(staged=True, task="review")
            args = mock.call_args[0][0]
            assert "--staged" in args
            assert "--task" in args and "review" in args


class TestRoamAgentPlanArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_agent_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default output_format='plain' mirrors CLI per LAW 11.
            roam_agent_plan(agents=3)
            mock.assert_called_once_with(["agent-plan", "--agents", "3", "--format", "plain"], ".")

    def test_format_override(self) -> None:
        from roam.mcp_server import roam_agent_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_agent_plan(agents=5, output_format="claude-teams")
            args = mock.call_args[0][0]
            assert "--agents" in args and "5" in args
            assert "--format" in args and "claude-teams" in args


class TestRoamAgentContextArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_agent_context

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default agents=0 -> CLI picks max(agent_id, 2) per LAW 11.
            roam_agent_context(agent_id=1)
            mock.assert_called_once_with(["agent-context", "--agent-id", "1"], ".")

    def test_explicit_agents(self) -> None:
        from roam.mcp_server import roam_agent_context

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_agent_context(agent_id=2, agents=4)
            args = mock.call_args[0][0]
            assert "--agent-id" in args and "2" in args
            assert "--agents" in args and "4" in args


class TestRoamAgentScoreArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_agent_score

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults: agent='', since='', top=0 -> bare subcommand.
            roam_agent_score()
            mock.assert_called_once_with(["agent-score"], ".")

    def test_agent_filter(self) -> None:
        from roam.mcp_server import roam_agent_score

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_agent_score(agent="claude")
            args = mock.call_args[0][0]
            assert "--agent" in args and "claude" in args

    def test_since_and_top(self) -> None:
        from roam.mcp_server import roam_agent_score

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_agent_score(since="2026-05-01T00:00:00Z", top=5)
            args = mock.call_args[0][0]
            assert "--since" in args and "2026-05-01T00:00:00Z" in args
            assert "--top" in args and "5" in args


class TestRoamGuardArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_guard

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_guard(symbol="handleSave")
            mock.assert_called_once_with(["guard", "handleSave"], ".")


class TestRoamAdversarialArgShape:
    # B6: roam_adversarial is now ``async def`` (compress_mode dispatch).
    # The default ``compress_mode="off"`` path returns the deterministic
    # envelope before any await, so driving it via ``asyncio.run`` leaves
    # the underlying ``_run_roam`` arg shape unchanged.
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_adversarial

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Defaults: severity='low', output_format='text' per LAW 11.
            asyncio.run(roam_adversarial())
            mock.assert_called_once_with(
                ["adversarial", "--severity", "low", "--format", "text"],
                ".",
            )

    def test_staged(self) -> None:
        from roam.mcp_server import roam_adversarial

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            asyncio.run(roam_adversarial(staged=True, severity="high"))
            args = mock.call_args[0][0]
            assert "--staged" in args
            assert "--severity" in args and "high" in args

    def test_commit_range_and_fail_on_critical(self) -> None:
        from roam.mcp_server import roam_adversarial

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            asyncio.run(
                roam_adversarial(
                    commit_range="main..HEAD",
                    fail_on_critical=True,
                    output_format="markdown",
                )
            )
            args = mock.call_args[0][0]
            assert "--range" in args and "main..HEAD" in args
            assert "--fail-on-critical" in args
            assert "--format" in args and "markdown" in args

    def test_compress_mode_off_is_default_passthrough(self) -> None:
        """Default ``compress_mode='off'`` returns the raw envelope verbatim
        (pure superset, LAW 11 — no sampling unless opted in)."""
        from roam.mcp_server import roam_adversarial

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True, "summary": {"verdict": "v"}}
            out = asyncio.run(roam_adversarial())
            assert out == {"ok": True, "summary": {"verdict": "v"}}
            assert "compress_mode_invalid" not in out["summary"]

    def test_compress_mode_invalid_stamps_sentinel(self) -> None:
        """An unknown ``compress_mode`` is loud, not a silent no-op
        (Pattern-1 variant D): the deterministic verdict is preserved and
        ``summary.compress_mode_invalid`` is stamped."""
        from roam.mcp_server import roam_adversarial

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True, "summary": {"verdict": "v"}}
            out = asyncio.run(roam_adversarial(compress_mode="bogus"))
            assert out["summary"]["compress_mode_invalid"] is True
            assert out["summary"]["verdict"] == "v"


class TestRoamMigrationPlanArgShape:
    def test_default_invocation(self) -> None:
        from roam.mcp_server import roam_migration_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            # Default max_risk='high' mirrors the CLI default per LAW 11.
            roam_migration_plan()
            mock.assert_called_once_with(["migration-plan", "--max-risk", "high"], ".")

    def test_input_path(self) -> None:
        """W332 canonical ``input_path`` -- YAML target-architecture spec."""
        from roam.mcp_server import roam_migration_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_migration_plan(input_path="target.yml", max_risk="medium")
            args = mock.call_args[0][0]
            assert args[0] == "migration-plan"
            assert "--target" in args and "target.yml" in args
            assert "--max-risk" in args and "medium" in args

    def test_inline_moves(self) -> None:
        from roam.mcp_server import roam_migration_plan

        with patch("roam.mcp_server._run_roam") as mock:
            mock.return_value = {"ok": True}
            roam_migration_plan(
                moves=("foo=src/a.py", "bar=src/b.py"),
                max_risk="low",
            )
            args = mock.call_args[0][0]
            assert args[0] == "migration-plan"
            assert args.count("--move") == 2
            assert "foo=src/a.py" in args
            assert "bar=src/b.py" in args
            assert "--max-risk" in args and "low" in args
