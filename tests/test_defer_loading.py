"""Tests for MCP tool defer_loading annotations.

Core tools and the meta-tool should NOT have deferLoading set.
All other tools should have deferLoading=True in their annotations.
This enables Claude Code's Tool Search feature for context reduction.
"""

from __future__ import annotations

import asyncio
import os
import importlib

import pytest


@pytest.fixture(scope="module")
def full_preset_tools():
    """Load MCP server with full preset and return tool list."""
    # Force full preset so all tools are registered
    env_backup = os.environ.get("ROAM_MCP_PRESET")
    os.environ["ROAM_MCP_PRESET"] = "full"
    try:
        import roam.mcp_server as ms
        importlib.reload(ms)

        if ms.mcp is None:
            pytest.skip("FastMCP not installed")

        loop = asyncio.new_event_loop()
        tools = loop.run_until_complete(ms.mcp.list_tools())
        loop.close()

        return tools, ms._CORE_TOOLS, ms._META_TOOL
    finally:
        if env_backup is None:
            os.environ.pop("ROAM_MCP_PRESET", None)
        else:
            os.environ["ROAM_MCP_PRESET"] = env_backup


def _get_defer_loading(tool) -> bool | None:
    """Extract deferLoading value from a tool's annotations."""
    if not tool.annotations:
        return None
    dump = tool.annotations.model_dump(exclude_none=True)
    return dump.get("deferLoading")


class TestDeferLoading:
    """Verify deferLoading annotations on MCP tools."""

    def test_core_tools_not_deferred(self, full_preset_tools):
        """Core preset tools must NOT have deferLoading set."""
        tools, core_names, _meta = full_preset_tools
        core_tools = [t for t in tools if t.name in core_names]

        assert len(core_tools) == len(core_names), (
            f"Expected {len(core_names)} core tools, got {len(core_tools)}. "
            f"Missing: {core_names - {t.name for t in core_tools}}"
        )

        for tool in core_tools:
            defer = _get_defer_loading(tool)
            assert defer is None or defer is False, (
                f"Core tool {tool.name} should NOT have deferLoading=True"
            )

    def test_meta_tool_not_deferred(self, full_preset_tools):
        """The meta-tool (roam_expand_toolset) must NOT have deferLoading."""
        tools, _core, meta_name = full_preset_tools
        meta_tools = [t for t in tools if t.name == meta_name]

        assert len(meta_tools) == 1, f"Expected 1 meta-tool, got {len(meta_tools)}"
        defer = _get_defer_loading(meta_tools[0])
        assert defer is None or defer is False, (
            f"Meta-tool {meta_name} should NOT have deferLoading=True"
        )

    def test_non_core_tools_deferred(self, full_preset_tools):
        """All non-core, non-meta tools MUST have deferLoading=True."""
        tools, core_names, meta_name = full_preset_tools
        always_loaded = core_names | {meta_name}
        non_core = [t for t in tools if t.name not in always_loaded]

        assert len(non_core) > 0, "Expected some non-core tools"

        missing_defer = []
        for tool in non_core:
            defer = _get_defer_loading(tool)
            if defer is not True:
                missing_defer.append(tool.name)

        assert missing_defer == [], (
            f"Non-core tools missing deferLoading=True: {missing_defer}"
        )

    def test_deferred_count(self, full_preset_tools):
        """Verify the exact split: 23 always-loaded, rest deferred."""
        tools, core_names, meta_name = full_preset_tools
        always_loaded = core_names | {meta_name}

        deferred = [t for t in tools if _get_defer_loading(t) is True]
        not_deferred = [t for t in tools if _get_defer_loading(t) is not True]

        assert len(not_deferred) == len(always_loaded), (
            f"Expected {len(always_loaded)} non-deferred tools, got {len(not_deferred)}: "
            f"{sorted(t.name for t in not_deferred)}"
        )
        assert len(deferred) == len(tools) - len(always_loaded), (
            f"Expected {len(tools) - len(always_loaded)} deferred tools, "
            f"got {len(deferred)}"
        )

    def test_deferred_tools_have_annotations_object(self, full_preset_tools):
        """Every deferred tool must have an annotations object (not None)."""
        tools, core_names, meta_name = full_preset_tools
        always_loaded = core_names | {meta_name}
        non_core = [t for t in tools if t.name not in always_loaded]

        for tool in non_core:
            assert tool.annotations is not None, (
                f"Non-core tool {tool.name} has no annotations object"
            )

    def test_all_tool_names_unique(self, full_preset_tools):
        """Sanity check: all tool names should be unique."""
        tools, _, _ = full_preset_tools
        names = [t.name for t in tools]
        assert len(names) == len(set(names)), (
            f"Duplicate tool names found: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_core_tools_match_preset(self, full_preset_tools):
        """Verify the _CORE_TOOLS set matches what we expect."""
        _, core_names, _ = full_preset_tools
        expected_core = {
            # compound operations (4)
            "roam_explore", "roam_prepare_change",
            "roam_review_change", "roam_diagnose_issue",
            # comprehension (5)
            "roam_understand", "roam_search_symbol",
            "roam_context", "roam_file_info", "roam_deps",
            # daily workflow (7)
            "roam_preflight", "roam_diff", "roam_pr_risk",
            "roam_affected_tests", "roam_impact", "roam_uses",
            "roam_syntax_check",
            # code quality (5)
            "roam_health", "roam_dead_code",
            "roam_complexity_report", "roam_diagnose", "roam_trace",
            # batch operations (2)
            "roam_batch_search", "roam_batch_get",
        }
        assert core_names == expected_core, (
            f"Core tools mismatch.\n"
            f"  Extra: {core_names - expected_core}\n"
            f"  Missing: {expected_core - core_names}"
        )

    def test_annotations_include_policy_hints(self, full_preset_tools):
        """Tools should expose read/write/idempotence hints via annotations."""
        tools, _, _ = full_preset_tools
        if not any(t.annotations is not None for t in tools):
            pytest.skip("tool annotations not available in this FastMCP build")

        required = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        for tool in tools:
            assert tool.annotations is not None, f"{tool.name} missing annotations object"
            ann = tool.annotations.model_dump(exclude_none=True)
            missing = required - set(ann.keys())
            assert not missing, f"{tool.name} missing annotation keys: {sorted(missing)}"

    def test_mutate_and_annotation_tools_not_read_only(self, full_preset_tools):
        """Known side-effect tools should advertise readOnlyHint=False."""
        tools, _, _ = full_preset_tools
        by_name = {t.name: t for t in tools}

        for name in ("roam_mutate", "roam_annotate_symbol", "roam_ingest_trace", "roam_vuln_map"):
            if name not in by_name:
                # Tool can be absent if optional command isn't available in this build.
                continue
            ann = by_name[name].annotations.model_dump(exclude_none=True)
            assert ann.get("readOnlyHint") is False, f"{name} should be readOnlyHint=False"

    def test_task_support_or_meta_hint_present_for_long_tools(self, full_preset_tools):
        """Long-running tools should advertise task support via execution or meta fallback."""
        tools, _, _ = full_preset_tools
        by_name = {t.name: t for t in tools}

        for name in ("roam_orchestrate", "roam_mutate", "roam_forecast", "roam_search_semantic"):
            if name not in by_name:
                continue
            tool = by_name[name]
            execution = tool.execution.model_dump(exclude_none=True) if tool.execution else {}
            meta = dict(tool.meta or {})
            task_support = execution.get("taskSupport") or meta.get("taskSupport")
            assert task_support in {"optional", "required", "forbidden"}, (
                f"{name} missing taskSupport hint in execution/meta"
            )
