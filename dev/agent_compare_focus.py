#!/usr/bin/env python3
"""Focused 2-agent comparison: vanilla vs roam-agent (MCP + identity prompt).

We have already eliminated `wired` (verbose TASK→TOOL prompt — lost
every metric across 12 tasks) and `roam-bash` (sharper today, but
bypasses the entire MCP receipt / mode-gate / integrity substrate that
roam-code's strategic surface is built on). roam-agent is the highest-
potential pick: lowest absolute cost in the prior round, identity-first
prompt with tuning headroom, and the only variant aligned with roam's
agent-OS posture.

This harness drops to 2 configs and deepens the task corpus from
lookup-style questions to multi-step SYNTHESIS questions: refactor
proposals, test code, design critiques, migration diffs. The aim is to
test whether roam still pays off when the answer demands real code-
generation, not just precise file:line retrieval.

Run:
    dev/.venv-agent/bin/python dev/agent_compare_focus.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

from agent_compare import ROAM_AGENT_SYSTEM, VANILLA_SYSTEM  # type: ignore[import-not-found]
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)


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


def _vanilla_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=VANILLA_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=20,
    )


def _roam_agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={"roam-code": {"type": "stdio", "command": "roam", "args": ["mcp"]}},
        system_prompt=ROAM_AGENT_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=20,
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


CONFIGS = [
    ("vanilla", _vanilla_options),
    ("roam-agent", _roam_agent_options),
]


# 10 DEEP tasks — every one demands multi-step synthesis (refactor
# proposal / test code / design rewrite / unified diff / verdict +
# reasoning). Pure lookup tasks have been dropped in favor of artifact-
# producing tasks where the model has to compose, not just retrieve.
TASKS: dict[str, str] = {
    "deep01_refactor_proposal": (
        "Propose a refactor: extract `_normalize_aliases` (and any "
        "private helpers it calls) from src/roam/mcp_server.py into a "
        "new module src/roam/mcp_extras/alias_norm.py. Output four "
        "bullets: (a) functions to move with current file:line, "
        "(b) imports that must change in mcp_server.py, (c) the blast "
        "radius if done wrong (cite caller count), (d) one safety "
        "command to run before AND after the move."
    ),
    "deep02_schema_drift": (
        "In src/roam/db/, compare the columns declared in schema.py's "
        "CREATE TABLE statements vs columns added via `_safe_alter` "
        "in connection.py. Find ONE table whose schema.py columns and "
        "connection.py migrations are inconsistent (a column added by "
        "migration but missing from the CREATE, or vice versa). Name "
        "the table, the missing column, and whether this affects fresh "
        "DBs, upgraded DBs, or both."
    ),
    "deep03_test_synthesis": (
        "Write a complete pytest test (15-35 lines, ready to drop into "
        "tests/) that asserts: 'Every @_tool wrapper registered in "
        "src/roam/mcp_server.py has declared side-effect metadata "
        "(_TOOL_METADATA entry with read_only / destructive / "
        "idempotent flags).' The test must import the actual registry "
        "and fail with a useful message naming any tool that's missing."
    ),
    "deep04_identity_rewrite": (
        "Per AGENTS.md LAW 11 (identity > step enumeration), tool "
        "descriptions should describe what a tool IS in one phrase. "
        "Scan the first 6 @_tool decorations in src/roam/mcp_server.py "
        "from top of file. Pick the WORST one by LAW 11. Quote its "
        "current description verbatim. Rewrite it in ≤20 words, "
        "identity-first, imperative voice. Give the file:line."
    ),
    "deep05_recursion_safety": (
        "In src/roam/graph/cycles.py, find the Tarjan SCC implementation. "
        "Is it iterative or recursive? On a graph with depth ~10,000 "
        "would it stack-overflow? Quote the deciding line of code with "
        "file:line, and give a one-sentence verdict (SAFE / UNSAFE / "
        "MITIGATED-BY-X)."
    ),
    "deep06_envelope_diff": (
        "Find 3 commands in src/roam/commands/cmd_*.py that emit a "
        "JSON envelope WITHOUT a `verdict` field in `summary`. For "
        "each, produce the minimal patch (unified diff format, 1-3 "
        "added lines per file) that adds an appropriate verdict. The "
        "verdict text should make sense for what that command does."
    ),
    "deep07_complexity_pick": (
        "Of `roam health`, `roam coupling`, `roam impact`, which has "
        "the worst worst-case complexity on a 100k-symbol repo? Answer "
        "with: (a) the algorithm at the heart of the dominant cost "
        "(quote the line + file:line), (b) the complexity class "
        "(O(n²) / O(n·e) / O(n log n) / ...), (c) one sentence on "
        "what an optimization would target."
    ),
    "deep08_redaction_ordering": (
        "AGENTS.md says: 'redactor scrubs secrets BEFORE output_hash "
        "is computed' (MCP-P0.1). Verify this ordering in "
        "src/roam/mcp_server.py. Trace the wrapper chain `_wrap_with_"
        "receipt` → redaction call → hash computation. Quote the line "
        "for each step with file:line. Give verdict: ORDER-CORRECT, "
        "ORDER-VIOLATED, or NOT-IMPLEMENTED."
    ),
    "deep09_safe_to_delete": (
        "Find one entire CLI command module (a `cmd_*.py` file in "
        "src/roam/commands/) that is 100% safe to delete: ZERO callers "
        "in src/, NOT listed in `_COMMANDS` in src/roam/cli.py, and "
        "ZERO corresponding tests in tests/. Confirm all three "
        "conditions with evidence and name the file. If none exists, "
        "name the candidate that came closest and explain which "
        "condition it fails."
    ),
    "deep10_compound_simplify": (
        "Read `_cr` and `_COMPOUND_REGISTRY` in src/roam/mcp_server.py. "
        "Propose ONE structural simplification that would reduce the "
        "registry's source-line count by ≥30%. Be specific: name which "
        "lines/symbols to consolidate, what the consolidated form "
        "would look like in 5-10 lines of code, and which guard test "
        "must keep passing (cite tests/test_*.py)."
    ),
}


async def run_agent(name: str, options: ClaudeAgentOptions, prompt: str) -> AgentRun:
    run = AgentRun(name=name)
    t0 = time.monotonic()
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        run.text_output += block.text
                    elif isinstance(block, ToolUseBlock):
                        run.tool_counts[block.name] += 1
            elif isinstance(msg, ResultMessage):
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


def _short(name: str) -> str:
    return name.replace("mcp__roam-code__", "roam:")


def _summary(c: Counter, top: int = 4) -> str:
    return ", ".join(f"{_short(k)}={v}" for k, v in c.most_common(top))


def _roam_pct(c: Counter) -> float:
    total = sum(c.values())
    return 100.0 * sum(v for k, v in c.items() if k.startswith("mcp__roam-code__")) / max(total, 1)


def _serialize(r: AgentRun) -> dict:
    d = asdict(r)
    d["tool_counts"] = dict(r.tool_counts)
    return d


async def run_wave(task_id: str, prompt: str) -> list[AgentRun]:
    print(f"\n{'=' * 72}\nDEEP TASK: {task_id}\n{'=' * 72}\n{prompt}\n")
    t0 = time.monotonic()
    runs = await asyncio.gather(*(run_agent(name, opts(), prompt) for name, opts in CONFIGS))
    print(f"  [parallel wall: {time.monotonic() - t0:.1f}s]")

    print(
        f"\n{'agent':<11} {'turns':>5} {'tools':>5} {'roam%':>6} "
        f"{'wall':>7} {'cost':>8} {'in':>8} {'out':>6} {'cache_r':>9}  top tools"
    )
    print("-" * 130)
    for r in runs:
        print(
            f"{r.name:<11} {r.total_turns:>5} {sum(r.tool_counts.values()):>5} "
            f"{_roam_pct(r.tool_counts):>5.0f}% {r.wall_time:>6.1f}s "
            f"${r.total_cost:>7.4f} {r.input_tokens:>8} {r.output_tokens:>6} "
            f"{r.cache_read_tokens:>9}  {_summary(r.tool_counts)}"
        )
    return runs


async def main() -> None:
    out_path = os.path.join(os.path.dirname(__file__), "agent_compare_focus_results.json")
    payload: dict[str, list[dict]] = {}
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                payload = json.load(f)
            done = [k for k in TASKS if k in payload]
            if done:
                print(f"[resume] skipping {len(done)} already-done waves: {done}")
        except Exception:
            payload = {}

    for task_id, prompt in TASKS.items():
        if task_id in payload:
            continue
        runs = await run_wave(task_id, prompt)
        payload[task_id] = [_serialize(r) for r in runs]
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[saved {task_id}]")

    print(f"\n{'=' * 72}\nALL DEEP TASKS COMPLETE\n{'=' * 72}\nResults: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
