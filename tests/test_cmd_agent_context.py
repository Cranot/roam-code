"""Behavioral tests for ``roam agent-context`` (``cmd_agent_context.py``).

The command extracts ONE worker's slice from the full agent plan (see
``cmd_agent_plan.build_agent_plan``) and assembles a focused context blob:
write scope, read-only deps, interface contracts, and coordination notes.

Phase 1 enumerated the observable behaviors; Phase 2 asserts each with
concrete I/O against a real indexed fixture. Coverage gaps that could not be
exercised deterministically are documented at the bottom of this file.

Selection semantics worth knowing (ground truth, 2026-06-20): the partition
manifest labels every partition of a low-complexity fixture ``Worker-1`` (the
load-balancer always picks the least-loaded agent, and all loads are 0.0), so
``--agent-id 1`` resolves to the FIRST task in plan order and every higher id
is "not found". That is the real, deterministic behavior these tests pin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# The four instructions every partition context always carries (the 5th,
# downstream-handoff note, is conditional). Mirrors the literal list in
# cmd_agent_context.agent_context.
BASE_INSTRUCTIONS = [
    "Edit only files in write_files.",
    "Treat read_only_dependencies as immutable unless a coordinated follow-up task is created.",
    "Preserve all interface_contracts while changing behavior.",
    "Before handoff, run `roam guard <key-symbol>` on 1-2 key symbols in this partition.",
]


@pytest.fixture(scope="module")
def agent_project(tmp_path_factory):
    """A small multi-module Python project indexed exactly once for the module.

    Two independent dependency chains (auth -> tokens, billing -> tax) plus an
    api/routes.py that imports across both, so partitioning produces multiple
    partitions with at least one cross-partition handoff edge.
    """
    proj = tmp_path_factory.mktemp("agent_context_proj")
    files = {
        "auth/login.py": "from auth.tokens import create_token\ndef authenticate(u, p): return create_token(u)\n",
        "auth/tokens.py": "def create_token(user): return 'tok'\ndef verify_token(t): return True\n",
        "billing/invoice.py": "from billing.tax import calc_tax\ndef create_invoice(order): return calc_tax(order)\n",
        "billing/tax.py": "def calc_tax(order): return order * 0.1\n",
        "api/routes.py": (
            "from auth.login import authenticate\n"
            "from billing.invoice import create_invoice\n"
            "def handle(r): authenticate(r, r); return create_invoice(r)\n"
        ),
        "models.py": "class User:\n    pass\nclass Order:\n    pass\n",
    }
    (proj / ".gitignore").write_text(".roam/\n")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def runner():
    return CliRunner()


def _run_json_raw(runner, args, cwd):
    """Invoke with --json and return (result, parsed_dict) WITHOUT asserting
    exit code, so error envelopes (which exit 1 but still emit JSON) parse."""
    result = invoke_cli(runner, args, cwd=cwd, json_mode=True)
    raw = getattr(result, "stdout", None) or result.output
    return result, json.loads(raw)


# ---------------------------------------------------------------------------
# Argument validation (Click layer)
# ---------------------------------------------------------------------------


def test_agent_id_is_required(agent_project, runner):
    result = invoke_cli(runner, ["agent-context"], cwd=agent_project)
    assert result.exit_code == 2
    assert "Missing option" in result.output and "--agent-id" in result.output


def test_agent_id_below_range_rejected(agent_project, runner):
    # IntRange(1, None) -> 0 is out of range, Click rejects before the command body.
    result = invoke_cli(runner, ["agent-context", "--agent-id", "0"], cwd=agent_project)
    assert result.exit_code == 2
    assert "range x>=1" in result.output


def test_agents_below_range_rejected(agent_project, runner):
    result = invoke_cli(runner, ["agent-context", "--agent-id", "1", "--agents", "0"], cwd=agent_project)
    assert result.exit_code == 2
    assert "range x>=1" in result.output


# ---------------------------------------------------------------------------
# JSON success path (agent 1 is the only deterministically-resolvable worker)
# ---------------------------------------------------------------------------


def test_json_envelope_shape(agent_project, runner):
    result = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    data = parse_json_output(result, command="agent-context")
    assert_json_envelope(data, command="agent-context")

    # Top-level payload keys the command promises.
    for key in (
        "agent",
        "write_files",
        "read_only_dependencies",
        "interface_contracts",
        "depends_on_partitions",
        "downstream_partitions",
        "key_symbols",
        "instructions",
        "coordination",
    ):
        assert key in data, f"missing payload key: {key}"

    agent = data["agent"]
    for key in ("agent_id", "partition_id", "task_id", "phase", "merge_rank", "objective"):
        assert key in agent, f"missing agent key: {key}"
    assert agent["agent_id"] == "Worker-1"
    assert agent["task_id"].startswith("T")


def test_json_summary_counts_match_payload_lengths(agent_project, runner):
    """The summary count fields are the dominant agent-decision signal (LAW 1);
    each MUST equal the length of the list it summarizes — a regression here
    silently misleads an agent about its write scope."""
    result = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    data = parse_json_output(result, command="agent-context")
    summary = data["summary"]

    assert summary["agent_id"] == 1
    assert summary["n_agents"] == 3
    assert summary["partial_success"] is False
    assert summary["write_files"] == len(data["write_files"])
    assert summary["read_only_dependencies"] == len(data["read_only_dependencies"])
    assert summary["contracts"] == len(data["interface_contracts"])
    assert summary["downstream_partitions"] == len(data["downstream_partitions"])
    assert summary["verdict"].startswith("context for Worker-1")
    assert "partition" in summary["verdict"]


def test_json_write_files_nonempty_and_lists_typed(agent_project, runner):
    result = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    data = parse_json_output(result, command="agent-context")
    # The first partition of this fixture owns real source files.
    assert isinstance(data["write_files"], list) and len(data["write_files"]) >= 1
    assert all(isinstance(f, str) for f in data["write_files"])
    for list_key in (
        "read_only_dependencies",
        "interface_contracts",
        "depends_on_partitions",
        "downstream_partitions",
        "key_symbols",
    ):
        assert isinstance(data[list_key], list)
    assert len(data["key_symbols"]) <= 5  # command truncates to 5


def test_json_instructions_base_four_always_present(agent_project, runner):
    result = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    data = parse_json_output(result, command="agent-context")
    instructions = data["instructions"]
    assert instructions[:4] == BASE_INSTRUCTIONS

    # 5th instruction is present iff there are downstream partitions.
    if data["downstream_partitions"]:
        assert len(instructions) == 5
        assert "downstream partitions" in instructions[4]
        # Each downstream partition is named as P<n> in the note.
        for pid in data["downstream_partitions"]:
            assert f"P{pid}" in instructions[4]
    else:
        assert len(instructions) == 4


def test_json_coordination_block(agent_project, runner):
    result = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    data = parse_json_output(result, command="agent-context")
    coord = data["coordination"]
    assert set(coord.keys()) == {"merge_sequence", "handoffs", "conflict_probability"}

    pid = data["agent"]["partition_id"]
    assert isinstance(coord["merge_sequence"], list) and pid in coord["merge_sequence"]
    assert 0.0 <= coord["conflict_probability"] <= 1.0

    # Handoffs are filtered to ONLY those touching this partition.
    for h in coord["handoffs"]:
        assert pid in (int(h["from_partition"]), int(h["to_partition"]))


def test_default_agents_for_id_one_is_two(agent_project, runner):
    """No --agents -> effective_agents = max(2, agent_id). For id 1 that's 2."""
    result = invoke_cli(runner, ["agent-context", "--agent-id", "1"], cwd=agent_project, json_mode=True)
    data = parse_json_output(result, command="agent-context")
    assert data["summary"]["n_agents"] == 2


def test_output_is_deterministic(agent_project, runner):
    """Two identical invocations produce byte-identical payloads (only the
    non-deterministic _meta block — timestamp / index_age — may differ)."""
    runs = []
    for _ in range(2):
        result = invoke_cli(
            runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
        )
        data = parse_json_output(result, command="agent-context")
        data.pop("_meta", None)
        runs.append(data)
    assert runs[0] == runs[1]


# ---------------------------------------------------------------------------
# Not-found path (agent id with no matching Worker-N label)
# ---------------------------------------------------------------------------


def test_not_found_text_exits_one_with_guidance(agent_project, runner):
    result = invoke_cli(runner, ["agent-context", "--agent-id", "9", "--agents", "2"], cwd=agent_project)
    assert result.exit_code == 1
    assert "Agent 9 not found in plan with 2 agents" in result.output
    assert "Try a larger --agents value" in result.output


def test_not_found_json_envelope(agent_project, runner):
    result, data = _run_json_raw(runner, ["agent-context", "--agent-id", "9", "--agents", "2"], cwd=agent_project)
    assert result.exit_code == 1
    assert data["command"] == "agent-context"
    msg = "Agent 9 not found in plan with 2 agents. Try a larger --agents value."
    # Pattern-1(D): structured failure signal, not a silent SAFE verdict.
    assert data["error"] == msg
    assert data["summary"]["verdict"] == msg
    assert data["summary"]["agent_id"] == 9
    assert data["summary"]["n_agents"] == 2


def test_not_found_default_agents_uses_max(agent_project, runner):
    """Even on the not-found path the disclosed n_agents reflects the
    max(2, agent_id) default — id 5 with no --agents -> 5."""
    result, data = _run_json_raw(runner, ["agent-context", "--agent-id", "5"], cwd=agent_project)
    assert result.exit_code == 1
    assert data["summary"]["n_agents"] == 5
    assert "with 5 agents" in data["error"]


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def test_text_output_structure(agent_project, runner):
    result = invoke_cli(runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project)
    assert result.exit_code == 0
    out = result.output
    assert out.startswith("AGENT CONTEXT: Worker-1  (partition P")
    assert "phase " in out.splitlines()[0]
    assert "Objective:" in out
    assert "Write files (" in out
    assert "Read-only dependencies (" in out
    assert "Interface contracts (" in out
    assert "Execution guidance:" in out
    # The base instructions are rendered as bullets.
    for instr in BASE_INSTRUCTIONS:
        assert f"  - {instr}" in out


def test_text_empty_read_only_shows_none(agent_project, runner):
    """Agent 1 of this fixture has no read-only deps -> the section must
    render the explicit '(none)' placeholder, never an empty list."""
    # Confirm via JSON that the precondition holds, then assert the text marker.
    jresult = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    jdata = parse_json_output(jresult, command="agent-context")
    assert jdata["read_only_dependencies"] == []

    result = invoke_cli(runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project)
    body = result.output.split("Read-only dependencies (0):", 1)[1]
    assert body.lstrip().startswith("- (none)")


def test_text_lists_write_files(agent_project, runner):
    jresult = invoke_cli(
        runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project, json_mode=True
    )
    jdata = parse_json_output(jresult, command="agent-context")

    result = invoke_cli(runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project)
    for fpath in jdata["write_files"]:
        assert f"  - {fpath}" in result.output


def test_budget_truncates_text_output(agent_project, runner):
    """A tiny --budget forces budget_truncate to cut the body and append the
    truncation notice while keeping exit 0."""
    result = invoke_cli(
        runner, ["--budget", "15", "agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project
    )
    assert result.exit_code == 0
    assert "... truncated (budget: 15 tokens" in result.output
    # Full (untruncated) output is materially longer than the budgeted one.
    full = invoke_cli(runner, ["agent-context", "--agent-id", "1", "--agents", "3"], cwd=agent_project)
    assert len(result.output) < len(full.output)


# ---------------------------------------------------------------------------
# Phase 3 — coverage gaps that could NOT be exercised deterministically:
#
# 1. Worker selection for --agent-id >= 2. The partition load-balancer assigns
#    every partition of a zero-complexity fixture to "Worker-1", so no fixture
#    of reasonable size reliably yields a Worker-2 label to select. The
#    not-found tests pin the only deterministic alternative outcome. (This is
#    arguably a latent product limitation in cmd_agent_context's
#    `Worker-{agent_id}` match, not a test defect.)
# 2. The text "(+N more)" truncation branches (write_files / read_only > 25).
#    Cannot deterministically partition >25 files onto the first worker.
# 3. The interface_contracts "(none)" branch and a non-empty
#    read_only_dependencies / depends_on_partitions block — these belong to a
#    *downstream* partition that is unreachable given limitation (1) above.
# ---------------------------------------------------------------------------
