#!/usr/bin/env python3
"""10-wave wide comparison: roam-agent vs vanilla vs wired vs roam-bash.

Builds on dev/agent_compare.py. The 4 agent configs are identical (same
system prompts, same tool surfaces, same model), but the task corpus is
10 deeper coding-comprehension tasks that demand multi-step reasoning,
file:line precision, and cross-symbol synthesis.

Existing tasks (coupling, param_alias_bug) are NOT re-run — vanilla and
peers were already captured in dev/agent_compare_results.json. The
aggregation step at the end merges both files.

Within each wave the 4 agents run in PARALLEL (asyncio.gather). Waves
run sequentially. Results are written incrementally to
dev/agent_compare_wide_results.json so a crash mid-run leaves partial
data on disk.

Run:
    dev/.venv-agent/bin/python dev/agent_compare_wide.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

# Reuse system prompts from the v1 harness — keep agents identical so
# only the task corpus is the new variable.
from agent_compare import (  # type: ignore[import-not-found]
    ROAM_AGENT_SYSTEM,
    ROAM_BASH_SYSTEM,
    VANILLA_SYSTEM,
    WIRED_SYSTEM,
)
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
        max_turns=15,
    )


def _wired_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={"roam-code": {"type": "stdio", "command": "roam", "args": ["mcp"]}},
        system_prompt=WIRED_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


def _roam_agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={"roam-code": {"type": "stdio", "command": "roam", "args": ["mcp"]}},
        system_prompt=ROAM_AGENT_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


def _roam_bash_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=ROAM_BASH_SYSTEM,
        permission_mode="bypassPermissions",
        model="claude-sonnet-4-6",
        max_turns=15,
    )


CONFIGS = [
    ("vanilla", _vanilla_options),
    ("wired", _wired_options),
    ("roam-agent", _roam_agent_options),
    ("roam-bash", _roam_bash_options),
]


# 10 deep coding-comprehension tasks. Each demands multi-step reasoning
# AND verifiable file:line / count precision.
TASKS: dict[str, str] = {
    "wave01_multi_symbol_blast": (
        "For these 4 symbols defined in src/roam/: `_PARAM_ALIASES`, "
        "`_TOOL_METADATA`, `_COMPOUND_REGISTRY`, `_normalize_aliases` — "
        "count callers of each (in src/ AND tests/) and identify which "
        "has the largest blast radius if removed. Report a 4-line "
        "count table, then one sentence naming the winner with its number."
    ),
    "wave02_layering_violation": (
        "List any file in src/roam/commands/ that imports from "
        "src/roam/mcp_server.py — that would be a layering violation. "
        "Give file:line of each violating import, or explicitly confirm "
        "ZERO violations with one line of evidence."
    ),
    "wave03_dead_function": (
        "Find one function in src/roam/graph/*.py with >=15 lines AND zero "
        "callers anywhere in src/ or tests/ (excluding its own defining "
        "file). Report: function name, file:line, approx line count, and "
        "explicit proof of zero external callers."
    ),
    "wave04_test_gap_audit": (
        "Pick any 5 modules from src/roam/commands/cmd_*.py. For each, "
        "report whether there exists a test file under tests/ that "
        "imports or exercises that module's command. Output 5 lines, "
        "each `<module_basename> → <test_file_or_NONE>`."
    ),
    "wave05_imperative_lint": (
        "AGENTS.md LAW 2 says MCP tool descriptions must use IMPERATIVE "
        "voice ('Run X', 'Find Y') not DECLARATIVE ('This command...', "
        "'A tool that...'). Scan src/roam/mcp_server.py @_tool decorations "
        "and find ONE tool whose description starts declaratively. Give: "
        "tool name, file:line of the decoration, the offending opening "
        "words verbatim, and a one-sentence imperative rewrite."
    ),
    "wave06_module_cycle": (
        "Are there any import cycles between src/roam/commands/ and "
        "src/roam/mcp_extras/? If yes, name one cycle as file → file → "
        "file. If no, prove it by stating the direction of imports "
        "between the two directories in one sentence."
    ),
    "wave07_cli_mcp_drift": (
        "Count entries in `_COMMANDS` dict in src/roam/cli.py vs count of "
        "@_tool wrappers in src/roam/mcp_server.py. Report both numbers. "
        "Then name 3 CLI commands that have NO MCP wrapper and classify "
        "each by the skip-taxonomy in AGENTS.md (setup / local-state / "
        "daemon / repl)."
    ),
    "wave08_trace_alias_path": (
        "When `roam_search_symbol(pattern='Foo')` is invoked over MCP, "
        "the `pattern` keyword must be aliased to `query` before the "
        "underlying CLI runs. Trace the call chain from the FastMCP "
        "tool dispatcher to where `pattern` is normalized (or dropped). "
        "List each function in order with file:line."
    ),
    "wave09_schema_migration_coverage": (
        "Find every `_safe_alter` call in src/roam/db/. Group by target "
        "table name. For each table, state whether ANY test under tests/ "
        "asserts the migrated column exists (grep for the column name "
        "is enough evidence). Report one line per distinct table."
    ),
    "wave10_envelope_contract": (
        "AGENTS.md requires every JSON envelope summary to include a "
        "`verdict` field. Find calls to `json_envelope(...)` in "
        "src/roam/commands/ that do NOT pass `summary={..., 'verdict': "
        "...}`. Give 3 violating examples (file:line) OR confirm zero "
        "violations with one line of evidence."
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


def _serialize_run(r: AgentRun) -> dict:
    d = asdict(r)
    d["tool_counts"] = dict(r.tool_counts)
    return d


async def run_wave(task_id: str, prompt: str) -> list[AgentRun]:
    print(f"\n{'=' * 72}\nWAVE: {task_id}\n{'=' * 72}\n{prompt}\n")
    t0 = time.monotonic()
    runs = await asyncio.gather(*(run_agent(name, opts_fn(), prompt) for name, opts_fn in CONFIGS))
    wall = time.monotonic() - t0
    print(f"  [parallel wall: {wall:.1f}s]")

    print(
        f"\n{'agent':<12} {'turns':>5} {'tools':>5} {'roam%':>6} "
        f"{'wall':>7} {'cost':>8} {'in':>8} {'out':>6} {'cache_r':>9}  top tools"
    )
    print("-" * 130)
    for r in runs:
        print(
            f"{r.name:<12} {r.total_turns:>5} {sum(r.tool_counts.values()):>5} "
            f"{_roam_pct(r.tool_counts):>5.0f}% {r.wall_time:>6.1f}s "
            f"${r.total_cost:>7.4f} {r.input_tokens:>8} {r.output_tokens:>6} "
            f"{r.cache_read_tokens:>9}  {_summary(r.tool_counts)}"
        )
    return runs


async def main() -> None:
    out_path = os.path.join(os.path.dirname(__file__), "agent_compare_wide_results.json")
    payload: dict[str, list[dict]] = {}

    # Resume support: if the file exists, skip tasks already present.
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
        payload[task_id] = [_serialize_run(r) for r in runs]
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[saved {task_id} → {out_path}]")

    print(f"\n{'=' * 72}\nALL WAVES COMPLETE\n{'=' * 72}")
    print(f"Results: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
