#!/usr/bin/env python3
"""roam-agent — daily-driver Claude SDK wrapper with a mode dropdown.

Modes (the dropdown):
    mcp      [DEFAULT — winner]  MCP server + identity prompt
                                 +30% cheaper per char of answer than
                                 vanilla; zero max-turn failures across
                                 22 benchmarked tasks (dev/agent_compare*).
    cli                          Bash + `roam --json` recipes
                                 Sharper on one-shot lookups; bypasses
                                 MCP receipts / mode-gates / integrity.
    vanilla                      No roam at all (Bash/Read/Grep baseline)
                                 Fine for pure file-text tasks.
    wired                        MCP + verbose TASK→TOOL map
                                 Kept for A/B only — slower than `mcp`.

CLI:
    roam-agent                              # mcp mode, REPL
    roam-agent "your question"              # mcp mode, one-shot
    roam-agent --mode cli "..."             # pick at launch
    roam-agent --mode=vanilla "..."         # equivalent form
    echo "..." | roam-agent                 # stdin pipe

REPL slash commands (the in-session dropdown):
    /help            show this help
    /modes           list modes + active marker
    /mode <name>     switch mode (resets conversation, keeps session metrics)
    /status          session metrics so far
    /clear           reset conversation (keep mode)
    /quit            exit (also empty line / Ctrl+D)
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    ThinkingConfigDisabled,
    ToolUseBlock,
)


# --- System prompts (kept identical to dev/agent_compare.py so the daily
#     driver matches the harness that picked the winner) -------------------

# MCP_SYSTEM_PROMPT — locked from round 4 of the multi-repo bake-off.
# Wins by 30% cost-per-character vs vanilla and 30% vs the original tight
# prompt across roam-code (Python), union-frontend (Vue/TS), and
# union-backend (Laravel/PHP). Zero max-turn failures across 12 tasks.
# See dev/agent_compare_multirepo_results_round4.json for the bake-off
# data and dev/agent_compare_multirepo_scoreboard.py for the scoreboard.
# v7 prompt — locked after v8 regressed both focus (8/10 -> 3/8 R-wins)
# and multirepo (7/12 -> 2/12 R-wins). Keep in sync with ROAM_AGENT_SYSTEM
# in dev/agent_compare.py.
MCP_SYSTEM_PROMPT = (
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

CLI_SYSTEM_PROMPT = (
    "roam-code expert. Be FAST. "
    "PARALLEL: when you have multiple independent tool calls, emit them all in ONE turn "
    "(multiple tool_use blocks in a single assistant message). Never serialize independent reads/greps. "
    "Use `roam` CLI via Bash for structural queries. Pass `--json` BEFORE the subcommand. "
    "Recipes (copy verbatim, substitute FILE/SYMBOL/N): "
    "`roam --json coupling -n 500 | jq '[.pairs[] | select(.file_a==\"FILE\" or .file_b==\"FILE\")] | sort_by(-.strength) | .[0:N]'` (top-N coupling for FILE), "
    "`roam --json deps FILE` (imports), "
    "`roam --json uses SYMBOL` (callers), "
    "`roam --json search PATTERN` (find symbol by name substring), "
    "`roam --json impact SYMBOL` (blast radius). "
    "Bug-find: parallel Greps for ALL candidate symbols in turn 1; parallel Reads for ALL named files in turn 2. Synthesize in turn 3. "
    "Text content otherwise: Grep + Read. "
    "Stop on first sufficient answer. No cross-checking. No preamble."
)

VANILLA_SYSTEM_PROMPT = "You are a helpful coding assistant. Be terse and accurate."

WIRED_SYSTEM_PROMPT = (
    "You are a helpful coding assistant in a roam-code-enabled repo.\n\n"
    "# roam TASK→TOOL map\n\n"
    "- Multi-symbol search (3+ symbols) → mcp__roam-code__roam_batch_search\n"
    "- File/module coupling → mcp__roam-code__roam_coupling + roam_deps PARALLEL\n"
    "- Natural-language / conceptual → mcp__roam-code__roam_search_semantic\n"
    "- Single-symbol lookup → mcp__roam-code__roam_search_symbol\n"
    "- Impact / refactor blast → mcp__roam-code__roam_impact + roam_uses PARALLEL\n"
    "- Free-form retrieval → mcp__roam-code__roam_retrieve\n\n"
    "Be terse."
)


@dataclass(frozen=True)
class Mode:
    name: str
    blurb: str
    needs_mcp: bool
    prompt: str

    def options(self, model_override: str | None = None) -> ClaudeAgentOptions:
        common: dict[str, Any] = {
            "system_prompt": self.prompt,
            "permission_mode": "bypassPermissions",
            "model": model_override or "claude-sonnet-4-6",
            "max_turns": 20,
            "include_partial_messages": True,
            "thinking": {"type": "disabled"},
        }
        if self.needs_mcp:
            return ClaudeAgentOptions(
                mcp_servers={"roam-code": {"type": "stdio", "command": "roam", "args": ["mcp"]}},
                env={"ENABLE_TOOL_SEARCH": "false"},
                **common,
            )
        return ClaudeAgentOptions(**common)


MODES: dict[str, Mode] = {
    "mcp": Mode(
        "mcp",
        "MCP server + identity prompt (DEFAULT — winner)",
        True,
        MCP_SYSTEM_PROMPT,
    ),
    "cli": Mode(
        "cli",
        "Bash + roam --json recipes (sharper on lookups, bypasses MCP receipts)",
        False,
        CLI_SYSTEM_PROMPT,
    ),
    "vanilla": Mode(
        "vanilla",
        "No roam — Bash/Read/Grep only (baseline)",
        False,
        VANILLA_SYSTEM_PROMPT,
    ),
    "wired": Mode(
        "wired",
        "MCP + verbose TASK→TOOL map (slower than mcp; A/B only)",
        True,
        WIRED_SYSTEM_PROMPT,
    ),
}

DEFAULT_MODE = "mcp"


def _format_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for k, v in args.items():
        s = v if isinstance(v, str) else repr(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
        if sum(len(p) for p in parts) > 80:
            break
    return ", ".join(parts)


class Session:
    """Cumulative session metrics that persist across mode switches."""

    def __init__(self) -> None:
        self.tool_counts: Counter[str] = Counter()
        self.total_cost = 0.0
        self.total_turns = 0
        self._streaming = False
        self.mode_history: list[str] = []

    def record_mode(self, name: str) -> None:
        if not self.mode_history or self.mode_history[-1] != name:
            self.mode_history.append(name)

    async def consume(self, stream) -> bool:
        async for msg in stream:
            if isinstance(msg, StreamEvent):
                evt = msg.event or {}
                etype = evt.get("type")
                if etype == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "text_delta":
                        sys.stdout.write(delta.get("text", ""))
                        sys.stdout.flush()
                        self._streaming = True
                elif etype == "content_block_stop" and self._streaming:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    self._streaming = False
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        self.tool_counts[block.name] += 1
                        args = _format_args(block.input)
                        short = block.name.replace("mcp__roam-code__", "")
                        if self._streaming:
                            sys.stdout.write("\n")
                            self._streaming = False
                        print(f"  [→ {short}({args})]")
            elif isinstance(msg, ResultMessage):
                cost = msg.total_cost_usd or 0.0
                self.total_cost += cost
                self.total_turns += msg.num_turns
                print(
                    f"\n[turn · {msg.num_turns} steps · {msg.duration_ms}ms · "
                    f"${cost:.4f} · session ${self.total_cost:.4f}]"
                )
                return bool(msg.is_error)
        return False


def _list_modes(active: str) -> str:
    lines = ["modes (dropdown):"]
    for name, m in MODES.items():
        marker = "→" if name == active else " "
        lines.append(f"  {marker} {name:<8} {m.blurb}")
    return "\n".join(lines)


REPL_HELP = """slash commands:
  /help            show this help
  /modes           list modes + active marker
  /mode <name>     switch mode (resets conversation; preserves session metrics)
  /status          session metrics so far
  /clear           reset conversation (keep mode)
  /quit            exit (also empty line / Ctrl+D)"""


async def run_once(prompt: str, mode_name: str, model: str | None = None) -> int:
    sess = Session()
    sess.record_mode(mode_name)
    mode = MODES[mode_name]
    tag = mode.name + (f"/{model.split('-')[1]}" if model else "")
    print(f"[mode={tag}] USER: {prompt}\n")
    async with ClaudeSDKClient(options=mode.options(model)) as client:
        await client.query(prompt)
        had_error = await sess.consume(client.receive_response())
    return 1 if had_error else 0


async def run_repl(initial_mode: str) -> int:
    sess = Session()
    sess.record_mode(initial_mode)
    active = initial_mode
    print(
        f"roam-agent REPL — mode={active}. "
        f"Type /help for slash commands, /quit (or empty line) to exit.\n"
    )

    client = ClaudeSDKClient(options=MODES[active].options())
    await client.__aenter__()
    try:
        while True:
            try:
                line = input(f"[{active}] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line or line == "/quit":
                break
            if line == "/help":
                print(REPL_HELP)
                continue
            if line == "/modes":
                print(_list_modes(active))
                continue
            if line == "/status":
                print(
                    f"session: turns={sess.total_turns} "
                    f"cost=${sess.total_cost:.4f} "
                    f"tools={dict(sess.tool_counts)} "
                    f"modes={' → '.join(sess.mode_history)}"
                )
                continue
            if line.startswith("/mode "):
                new_mode = line.split(maxsplit=1)[1].strip()
                if new_mode not in MODES:
                    print(f"unknown mode: {new_mode!r}. available: {', '.join(MODES)}")
                    continue
                if new_mode == active:
                    print(f"already in {new_mode}")
                    continue
                await client.__aexit__(None, None, None)
                active = new_mode
                sess.record_mode(active)
                client = ClaudeSDKClient(options=MODES[active].options())
                await client.__aenter__()
                print(f"[switched → {active}] (conversation reset; session metrics retained)")
                continue
            if line == "/clear":
                await client.__aexit__(None, None, None)
                client = ClaudeSDKClient(options=MODES[active].options())
                await client.__aenter__()
                print("[conversation reset]")
                continue
            try:
                await client.query(line)
                await sess.consume(client.receive_response())
            except Exception as e:
                print(f"[error: {e}]")
    finally:
        await client.__aexit__(None, None, None)
    print(
        f"\n--- session · {sess.total_turns} steps · ${sess.total_cost:.4f} · "
        f"tools: {dict(sess.tool_counts)} · modes: {' → '.join(sess.mode_history)} ---"
    )
    return 0


MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
    "haiku": "claude-haiku-4-5-20251001",
}


def _parse_args(args: list[str]) -> tuple[str, list[str], bool, str | None]:
    mode = DEFAULT_MODE
    model: str | None = None
    rest: list[str] = []
    show_help = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            show_help = True
            i += 1
        elif a == "--mode":
            if i + 1 >= len(args):
                print("--mode requires a value", file=sys.stderr)
                sys.exit(2)
            mode = args[i + 1]
            i += 2
        elif a.startswith("--mode="):
            mode = a.split("=", 1)[1]
            i += 1
        elif a == "--model":
            if i + 1 >= len(args):
                print("--model requires a value", file=sys.stderr)
                sys.exit(2)
            model = MODEL_ALIASES.get(args[i + 1], args[i + 1])
            i += 2
        elif a.startswith("--model="):
            v = a.split("=", 1)[1]
            model = MODEL_ALIASES.get(v, v)
            i += 1
        elif a == "--haiku":
            model = MODEL_ALIASES["haiku"]
            i += 1
        else:
            rest.append(a)
            i += 1
    if mode not in MODES:
        print(
            f"unknown mode: {mode!r}. available: {', '.join(MODES)}",
            file=sys.stderr,
        )
        sys.exit(2)
    return mode, rest, show_help, model


def main() -> int:
    mode, rest, show_help, model = _parse_args(sys.argv[1:])
    if show_help:
        assert __doc__ is not None
        print(__doc__)
        print(_list_modes(mode))
        return 0
    if rest:
        return asyncio.run(run_once(" ".join(rest), mode, model))
    if not sys.stdin.isatty():
        return asyncio.run(run_once(sys.stdin.read().strip(), mode, model))
    return asyncio.run(run_repl(mode))


if __name__ == "__main__":
    sys.exit(main())
