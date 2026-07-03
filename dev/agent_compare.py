#!/usr/bin/env python3
"""3-agent comparison harness.

Runs the same task across three configurations using the same SDK,
so the only thing varying is the agent's tool surface + system prompt.

Agents:
    vanilla     — no roam MCP, no roam-aware system prompt. Bash/Read/Grep.
    wired       — roam MCP exposed + CLAUDE.md-equivalent TASK→TOOL system prompt.
    roam-agent  — roam MCP exposed + tight roam-first system prompt (our dev/roam_agent.py).

Run:
    dev/.venv-agent/bin/python dev/agent_compare.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import import_module
from typing import Any

VANILLA_SYSTEM = "You are a helpful coding assistant. Be terse and accurate."


WIRED_SYSTEM = """You are a helpful coding assistant in a roam-code-enabled repo.

# roam TASK→TOOL map (verified empirical winners, 2026-05-23)

- Multi-symbol search (3+ symbols) → `mcp__roam-code__roam_batch_search`
- File/module coupling → `mcp__roam-code__roam_coupling` + `roam_deps` in PARALLEL
- Natural-language / conceptual → `mcp__roam-code__roam_search_semantic`
- Design patterns → `mcp__roam-code__roam_patterns`
- Project health → `mcp__roam-code__roam_alerts` + `roam_health` + `roam_dashboard` in PARALLEL
- Single-symbol lookup → `mcp__roam-code__roam_search_symbol` (replaces grep)
- Single-symbol callers → `mcp__roam-code__roam_uses`
- File role → `mcp__roam-code__roam_understand` or `roam_file_info`
- Files to read → `mcp__roam-code__roam_context`
- Impact / refactor blast → `mcp__roam-code__roam_impact` + `roam_uses` in PARALLEL
- Pre-edit safety → `mcp__roam-code__roam_prepare_change`
- Free-form retrieval → `mcp__roam-code__roam_retrieve`
- Code-comprehension question, unsure which tool → `mcp__roam-code__roam_ask` (intent dispatcher)

AVOID: roam_for_*, roam_complexity_report, roam_critique (without piped diff),
roam_test_impact, roam_smells. Use vanilla grep/Read when faster.

Prefer roam over Bash:grep / Read for code-comprehension. Be terse."""


# ROAM_AGENT_SYSTEM — v7 (terseness-tuned, FINAL after v8 regression).
# Trajectory: v6 lost 7/10 focus tasks on output verbosity. v7 added (1)
# SKIP-roam for synthesis, (2) <=400 word cap, (3) "no design commentary".
# Result: focus 3/10 -> 8/10 roam wins, multirepo 2/12 -> 7/12 roam wins,
# overall -22% cost vs vanilla across all 22 tasks. v8 attempted to fix the
# remaining trace/flow losses with "EXACTLY ONE roam_ call" + a trace recipe;
# both clauses backfired (focus 3/8, multirepo 2/12). v7 stays as the winner.
# See dev/agent_compare_focus_results_v7.json + dev/agent_compare_multirepo_results_v7.json.
ROAM_AGENT_SYSTEM = (
    "roam-code expert. Be FAST and TERSE. "
    "Structural lookups (coupling/callers/blast/impact/dead-code) -> one well-aimed roam_ call: "
    "roam_coupling+roam_deps PARALLEL for coupling, roam_uses for callers, "
    "roam_search_symbol (3+ -> roam_batch_search) for symbol lookup, "
    "roam_dead_code for unused, roam_impact for blast, roam_file_info for file role, roam_health for overview. "
    "Synthesis (write test/code/diff/proposal) and content tasks with named files -> SKIP roam, Read directly. "
    "Hard caps: 3 tool calls. Answer in <=400 words. "
    "No design commentary, no alternatives, no 'one could also...'. Answer the question asked. "
    "No cross-checking, no preamble."
)


ROAM_BASH_SYSTEM = (
    "roam-code expert. Be fast. Use `roam` CLI via Bash for structural queries. "
    "Always pass `--json` BEFORE the subcommand: `roam --json <cmd> ...`. "
    "Recipes (copy verbatim, substitute FILE/SYMBOL/N): "
    '`roam --json coupling -n 500 | jq \'[.pairs[] | select(.file_a=="FILE" or .file_b=="FILE")] | sort_by(-.strength) | .[0:N]\'` (top-N coupling for FILE), '
    "`roam --json deps FILE` (imports), "
    "`roam --json uses SYMBOL` (callers), "
    "`roam --json search PATTERN` (find symbol by name substring), "
    "`roam --json impact SYMBOL` (blast radius). "
    "Text search / file content use Grep + Read. "
    "Cap at 3 tool calls. Stop on first sufficient answer. No preamble."
)


@lru_cache(maxsize=1)
def _claude_agent_sdk() -> Any:
    try:
        return import_module("claude_agent_sdk")
    except ModuleNotFoundError as exc:
        if exc.name != "claude_agent_sdk":
            raise
        raise RuntimeError(
            "claude_agent_sdk is required to run dev/agent_compare.py. "
            "Install claude-agent-sdk into dev/.venv-agent before running the harness."
        ) from exc


@dataclass
class AgentRun:
    name: str
    tool_counts: Counter = field(default_factory=Counter)
    total_cost: float = 0.0
    total_turns: int = 0
    wall_time: float = 0.0
    text_output: str = ""
    is_error: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


def _vanilla_options() -> Any:
    sdk = _claude_agent_sdk()
    return sdk.ClaudeAgentOptions(
        system_prompt=VANILLA_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
    )


def _wired_options() -> Any:
    sdk = _claude_agent_sdk()
    return sdk.ClaudeAgentOptions(
        mcp_servers={
            "roam-code": {
                "type": "stdio",
                "command": "roam",
                "args": ["mcp"],
            }
        },
        system_prompt=WIRED_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


def _roam_agent_options() -> Any:
    sdk = _claude_agent_sdk()
    return sdk.ClaudeAgentOptions(
        mcp_servers={
            "roam-code": {
                "type": "stdio",
                "command": "roam",
                "args": ["mcp"],
            }
        },
        system_prompt=ROAM_AGENT_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


def _roam_bash_options() -> Any:
    sdk = _claude_agent_sdk()
    return sdk.ClaudeAgentOptions(
        system_prompt=ROAM_BASH_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
    )


async def run_agent(name: str, options: Any, prompt: str) -> AgentRun:
    sdk = _claude_agent_sdk()
    run = AgentRun(name=name)
    t0 = time.monotonic()
    try:
        async for msg in sdk.query(prompt=prompt, options=options):
            if isinstance(msg, sdk.AssistantMessage):
                for block in msg.content:
                    if isinstance(block, sdk.TextBlock):
                        run.text_output += block.text
                    elif isinstance(block, sdk.ToolUseBlock):
                        run.tool_counts[block.name] += 1
            elif isinstance(msg, sdk.ResultMessage):
                run.total_cost += msg.total_cost_usd or 0.0
                run.total_turns += msg.num_turns
                run.is_error = bool(msg.is_error)
                u = msg.usage or {}
                run.input_tokens += int(u.get("input_tokens", 0) or 0)
                run.output_tokens += int(u.get("output_tokens", 0) or 0)
                run.cache_creation_tokens += int(u.get("cache_creation_input_tokens", 0) or 0)
                run.cache_read_tokens += int(u.get("cache_read_input_tokens", 0) or 0)
    except Exception as e:
        run.is_error = True
        run.text_output += f"\n[exception: {e}]"
    run.wall_time = time.monotonic() - t0
    return run


def _short_tool(name: str) -> str:
    return name.replace("mcp__roam-code__", "roam:")


def _tools_summary(counter: Counter, top: int = 5) -> str:
    return ", ".join(f"{_short_tool(k)}={v}" for k, v in counter.most_common(top))


def _roam_call_count(counter: Counter) -> int:
    return sum(v for k, v in counter.items() if k.startswith("mcp__roam-code__"))


TASKS = {
    "coupling": (
        "Find the 5 files most strongly coupled to "
        "src/roam/mcp_server.py in this repo. "
        "'Coupling' = shared imports, call edges, co-changes. "
        "Report file, indicator, one-sentence coupling kind. "
        "End your answer with TOOLS_USED: <name>=<count>, ..."
    ),
    "param_alias_bug": (
        "There is a bug in this repo: roam_search_symbol(pattern=...) returns "
        "EMPTY_INPUT with the warning \"ignoring 'pattern' (use 'query' only)\" "
        "even though _PARAM_ALIASES in mcp_server.py defines "
        "{'query': {'pattern': 'query'}} which should rewrite the alias. "
        "Find the precise location of _PARAM_ALIASES (file:line) and the "
        "function that applies the rewrite (file:line). Explain in 2-3 "
        "sentences why the rewrite isn't happening. Do NOT modify any files."
    ),
}


async def compare(task_id: str) -> list[AgentRun]:
    prompt = TASKS[task_id]
    print(f"\n{'=' * 72}")
    print(f"TASK: {task_id}")
    print(f"{'=' * 72}")
    print(f"{prompt}\n")

    configs = [
        ("vanilla", _vanilla_options()),
        ("wired", _wired_options()),
        ("roam-agent", _roam_agent_options()),
        ("roam-bash", _roam_bash_options()),
    ]

    runs: list[AgentRun] = []
    for name, opts in configs:
        print(f"  [running {name}...]", flush=True)
        r = await run_agent(name, opts, prompt)
        roam_pct = 100.0 * _roam_call_count(r.tool_counts) / max(sum(r.tool_counts.values()), 1)
        print(
            f"  → {r.total_turns} turns, {r.wall_time:.1f}s, "
            f"${r.total_cost:.4f}, {sum(r.tool_counts.values())} tools "
            f"({roam_pct:.0f}% roam)"
        )
        runs.append(r)

    print(f"\n--- COMPARISON TABLE ({task_id}) ---")
    print(
        f"{'agent':<12} {'turns':>5} {'tools':>5} {'roam%':>6} "
        f"{'wall':>7} {'cost':>8} {'in':>8} {'out':>6} {'cache':>8}  top tools"
    )
    print("-" * 120)
    for r in runs:
        total = sum(r.tool_counts.values())
        roam_pct = 100.0 * _roam_call_count(r.tool_counts) / max(total, 1)
        print(
            f"{r.name:<12} {r.total_turns:>5} {total:>5} {roam_pct:>5.0f}% "
            f"{r.wall_time:>6.1f}s ${r.total_cost:>7.4f} "
            f"{r.input_tokens:>8} {r.output_tokens:>6} {r.cache_read_tokens:>8}  "
            f"{_tools_summary(r.tool_counts)}"
        )

    return runs


async def main() -> None:
    all_results: dict[str, list[AgentRun]] = {}
    for task_id in TASKS:
        all_results[task_id] = await compare(task_id)

    print(f"\n{'=' * 72}")
    print("OUTPUTS (first 600 chars each)")
    print(f"{'=' * 72}")
    for task_id, runs in all_results.items():
        for r in runs:
            print(f"\n--- {task_id} · {r.name} ---")
            text = r.text_output.strip()
            print(text[:600] + ("\n... [truncated]" if len(text) > 600 else ""))

    out_path = os.path.join(os.path.dirname(__file__), "agent_compare_results.json")
    payload = {
        task_id: [
            {
                "name": r.name,
                "tool_counts": dict(r.tool_counts),
                "total_cost": r.total_cost,
                "total_turns": r.total_turns,
                "wall_time": r.wall_time,
                "text_output": r.text_output,
                "is_error": r.is_error,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "cache_read_tokens": r.cache_read_tokens,
            }
            for r in runs
        ]
        for task_id, runs in all_results.items()
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n[full results saved to {out_path}]")


if __name__ == "__main__":
    asyncio.run(main())
