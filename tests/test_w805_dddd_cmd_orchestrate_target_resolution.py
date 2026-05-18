"""W805-DDDD - cmd_orchestrate vacuous-partition disclosure (W805 sweep).

Eighty-second-in-batch of the W805 sweep. Sibling pin to W805-BBBB (which
covered cmd_simulate's TARGET-side resolution gap on apply_move / apply_merge).
THIS letter probes the natural counterfactual-axis sister: cmd_orchestrate
INVENTS destination work-units (multi-agent partitioning per CLAUDE.md
"multi-agent work partitioning (Louvain-based)") rather than inventing
destination edges. The hypothesis was that cmd_orchestrate would show the
same TARGET-side resolution gap.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

Probed live behaviour FIVE times against a populated 2-file / 3-symbol corpus
(``a.py: helper, caller`` + ``b.py: other``) and a 1-file empty corpus
(``empty.py``):

1. Empty corpus + ``--agents 3`` -> exit 0, verdict
   "orchestrated 3 agents with 0 write conflicts across 0 shared interfaces",
   ``partial_success: false``. Three empty-stub agents (``empty-1`` /
   ``empty-2`` / ``empty-3``) with ``symbols_owned: 0``, ``write_files: []``,
   ``read_only_files: []``, ``contracts: []``. No disclosure that the corpus
   has zero symbols. Pattern-2 silent fallback.

2. Populated corpus + ``--agents 0`` -> exit 0, ``summary.n_agents: 1``,
   verdict "orchestrated 1 agents with 0 write conflicts...". User asked for
   0 agents; ``partition_for_agents`` silently coerced ``n_agents < 1``
   to 1 (graph/partition.py L33-34). The summary lies: it reports
   ``n_agents: 1`` as if it were the requested value, with no
   ``requested_n_agents: 0`` / ``partial_success: true`` /
   ``input_coerced: true``. Pattern-1-V-D silent input coercion.

3. Populated corpus + ``--agents 100`` (only 3 symbols available!) ->
   exit 0, ``n_agents: 100``, verdict "orchestrated 100 agents...".
   97 of 100 agents have ``symbols_owned: 0``. No disclosure that the
   request was structurally degenerate. Pattern-1-V-D silent degeneracy
   on degraded partitioning.

4. Populated corpus + ``--agents 3 --file totally/bogus/path.py`` ->
   exit 0, verdict "No matching files found" -> CORRECTLY disclosed.
   Sealed-today behaviour.

5. Populated corpus + ``--agents -1`` -> same coercion as PROBE 2.

W978 hypothesis shift
---------------------

The original hypothesis was "TARGET-side file resolution gap like
cmd_simulate". That hypothesis SHIFTED: cmd_orchestrate does NOT invent
destination files (no ``target_file`` argument). It groups EXISTING symbols
into agent partitions. The actual bug class is therefore **vacuous-partition
output + silent input coercion**:

- Empty-input -> silent success verdict (Pattern-2).
- Degenerate ``n_agents`` request -> silent coercion / silent degeneracy
  (Pattern-1-V-D).

Both are members of the SAME counterfactual-axis family as W805-BBBB:
"command that produces output structurally independent of input validity
without disclosing the lineage". W805-BBBB confirmed it for
graph-transform-target axis; W805-DDDD confirms it for partition-output
axis. Family confirmed (2 members).

W907 verify-cycle check
=======================

grep -i 'avoid.*cycle|circular import|kept local|lazy import' on
src/roam/commands/cmd_orchestrate.py + src/roam/graph/partition.py = NO
MATCHES. The two ``from roam.graph...`` lazy imports inside the function
body (lines 160-161) are unhedged - genuine click-startup latency lazy
imports, not cargo-cult cycle hedges. W907 clean.

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_dddd_cmd_orchestrate_target_resolution.py -x -n 0

Regression baseline:
    python -m pytest tests/test_orchestrate.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init_with_baseline(proj: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=proj,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=proj,
        check=True,
    )


def _invoke_orchestrate(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("orchestrate")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file - 0 symbols, 0 edges."""
    proj = tmp_path / "empty_orch_corpus_dddd"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def tiny_corpus(tmp_path, monkeypatch):
    """Indexed project with 3 symbols / 1 edge - small enough that --agents 100
    forces 97 empty partitions."""
    proj = tmp_path / "tiny_orch_corpus_dddd"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    (proj / "b.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestOrchestrateBogusInputsSealed:
    """Properties already satisfied by cmd_orchestrate today (regression-only)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """Smoke: empty corpus + --agents 3 exits 0 with JSON envelope."""
        result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        assert env["command"] == "orchestrate"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert verdict.strip(), f"empty verdict on empty corpus: {env!r}"

    def test_bogus_filter_path_names_gap_in_verdict(self, cli_runner, tiny_corpus):
        """Sealed-today: bogus --file IS disclosed via 'No matching files' verdict.

        Lock in the half that cmd_orchestrate already gets right - the
        --file filter miss IS named in the verdict text. The gap is the
        empty-corpus / degenerate-n_agents axes (see xfails below).
        """
        result = _invoke_orchestrate(
            cli_runner,
            tiny_corpus,
            "--agents",
            "3",
            "--file",
            "totally/bogus/path.py",
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"].lower()
        assert "no matching" in verdict or "no files" in verdict, (
            f"bogus --file filter must name the gap; got {verdict!r}"
        )

    def test_populated_corpus_real_partition(self, cli_runner, tiny_corpus):
        """Happy-path regression: real partition has full envelope shape."""
        result = _invoke_orchestrate(cli_runner, tiny_corpus, "--agents", "2", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        assert len(env["agents"]) == 2
        # At least one agent owns symbols on populated corpus
        owned = sum(a["symbols_owned"] for a in env["agents"])
        assert owned > 0, f"populated corpus must produce non-empty partitions; agents={env['agents']!r}"
        assert env["summary"]["verdict"].strip()


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - HIGH: empty corpus silently "succeeds"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-DDDD REAL BUG (HIGH, Pattern-2): graph/partition.py L46-47 "
        "+ _empty_result L162-181 fabricate N empty-stub agents on a "
        "zero-symbol corpus and return SAFE verdict 'orchestrated N agents "
        "with 0 write conflicts across 0 shared interfaces' with "
        "summary.partial_success=false. Agents reading the envelope cannot "
        "tell that the underlying graph is EMPTY (G.number_of_nodes() == 0) "
        "vs that the partition genuinely had no cross-agent conflicts. "
        "CLAUDE.md Pattern-2: 'Never emit verdict SAFE / completed when the "
        "underlying check failed or didn't run. Make absent state explicit: "
        "state: not_initialized.' Fix template: empty-corpus branch must "
        "emit verdict 'no symbols indexed' + summary.state='empty_corpus' + "
        "summary.partial_success=true. Separate fix wave."
    ),
)
def test_empty_corpus_partition_disclosure(cli_runner, empty_corpus):
    """Pin (Pattern-2): empty corpus MUST be disclosed via state or partial_success.

    Today: empty corpus produces N fabricated empty-stub agents and a
    success verdict structurally indistinguishable from a real-partition
    success on a graph with zero cross-partition conflicts.
    """
    result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    verdict = (summary.get("verdict") or "").lower()
    state = summary.get("state") or env.get("state")
    partial = summary.get("partial_success")
    # Either:
    #  (a) verdict explicitly names the empty corpus, OR
    #  (b) state is a closed-enum empty-corpus disclosure, OR
    #  (c) partial_success=True signals degraded output.
    discloses_empty = (
        "empty" in verdict
        or "no symbols" in verdict
        or "not initialized" in verdict
        or state in {"empty_corpus", "no_symbols", "not_initialized"}
        or partial is True
    )
    assert discloses_empty, (
        f"empty corpus produced SAFE verdict {verdict!r} with "
        f"state={state!r}, partial_success={partial!r}. Pattern-2: empty "
        f"input must be explicitly disclosed. agents={env['agents']!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #2 - HIGH: --agents 0 silently coerced to 1
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-DDDD REAL BUG (HIGH, Pattern-1-V-D): graph/partition.py L33-34 "
        "silently coerces n_agents<1 to 1 with NO disclosure to the caller. "
        "User invoking 'roam orchestrate --agents 0' gets summary.n_agents=1 "
        "+ verdict 'orchestrated 1 agents...' as if 1 were the requested "
        "value. Same for --agents -1 / --agents -5. CLAUDE.md Pattern-1-V-D: "
        "'Silent success on degraded resolution. Command resolves a target "
        "partially, proceeds to act on the degraded resolution, and emits "
        "a success verdict indistinguishable from a fully-resolved success.' "
        "Fix template: either (a) reject n_agents<1 with a usage_error "
        "envelope + recovery hint, or (b) disclose via summary.requested_n_agents=0 "
        "+ summary.input_coerced=true + partial_success=true. Separate fix wave."
    ),
)
def test_zero_agents_distinct_from_success(cli_runner, tiny_corpus):
    """Pin (Pattern-1-V-D): --agents 0 MUST be distinct from --agents 1.

    Today: --agents 0 and --agents 1 produce structurally identical envelopes
    (same n_agents=1, same verdict, same partition). Agents cannot tell their
    request was silently coerced.
    """
    result = _invoke_orchestrate(cli_runner, tiny_corpus, "--agents", "0", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    verdict = (summary.get("verdict") or "").lower()
    requested = summary.get("requested_n_agents") or summary.get("requested_agents")
    coerced = summary.get("input_coerced") or summary.get("coerced")
    partial = summary.get("partial_success")
    # Either:
    #  (a) verdict explicitly notes the coercion / invalid input, OR
    #  (b) summary.requested_n_agents=0 disclosed alongside n_agents=1, OR
    #  (c) input_coerced=true OR partial_success=true.
    disclosed = (
        "coerce" in verdict
        or "invalid" in verdict
        or "must be" in verdict
        or requested == 0
        or coerced is True
        or partial is True
    )
    assert disclosed, (
        f"--agents 0 silently coerced; summary={summary!r}. Pattern-1-V-D: input degradation must be disclosed."
    )


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #3 - MEDIUM: degenerate partitioning on excess agents
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-DDDD REAL BUG (MEDIUM, Pattern-1-V-D): graph/partition.py "
        "_adjust_cluster_count L213-219 + L243-245 fabricates empty agent "
        "partitions when n_agents > available_clusters. A 3-symbol corpus "
        "with --agents 100 emits 100 agents of which 97 have symbols_owned=0. "
        "Verdict reads 'orchestrated 100 agents with 0 write conflicts...' "
        "indistinguishable from a real 100-cluster partition. CLAUDE.md "
        "Pattern-1-V-D: degenerate-resolution success indistinguishable from "
        "fully-resolved success. Fix template: when count(empty_agents) > "
        "count(populated_agents) / threshold, set summary.partial_success=true "
        "+ verdict 'orchestrated N partitions (M empty stubs)' + "
        "summary.empty_partitions=M. Separate fix wave."
    ),
)
def test_excess_agents_degenerate_disclosure(cli_runner, tiny_corpus):
    """Pin (Pattern-1-V-D): excess --agents producing mostly-empty partitions
    MUST be disclosed via partial_success or empty_partitions count.

    Today: --agents 100 on a 3-symbol graph emits 97 empty stubs without
    disclosure.
    """
    result = _invoke_orchestrate(cli_runner, tiny_corpus, "--agents", "100", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    verdict = (summary.get("verdict") or "").lower()
    empty_count = sum(1 for a in env["agents"] if a["symbols_owned"] == 0)
    partial = summary.get("partial_success")
    empty_disclosed = (
        summary.get("empty_partitions")
        or summary.get("degenerate_agents")
        or "degenerate" in verdict
        or "empty" in verdict
        or "stub" in verdict
    )
    # Only assert the gap when there ARE empty agents on this corpus.
    assert empty_count > 0, f"setup broken: expected empty agents on tiny_corpus + --agents 100; got {empty_count}"
    assert partial is True or empty_disclosed, (
        f"--agents 100 on tiny corpus emitted {empty_count} empty stubs "
        f"with no disclosure; summary={summary!r}, "
        f"partial_success={partial!r}. Pattern-1-V-D: degenerate output "
        f"must be flagged."
    )


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #4 - MEDIUM: success envelope omits resolution field
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-DDDD REAL BUG (MEDIUM, Pattern-1-V-D shape gap, sister of "
        "W805-BBBB PIN #4): cmd_orchestrate.py L181-222 success envelope has "
        "NO 'resolution' field disclosing how the partition was achieved: "
        "did Louvain produce exactly n_agents clusters (direct)? Did "
        "_adjust_cluster_count merge smaller clusters (merged)? Did it split "
        "the largest cluster by betweenness (split)? Did it fall back to "
        "naive bisection on a betweenness exception (bisection_fallback)? "
        "All four paths emit the SAME success verdict shape. CLAUDE.md "
        "Pattern-1-V-D fix template: 'disclose the resolution state "
        "explicitly via a resolution field on the envelope (closed enum)'. "
        "Sister gap to cmd_simulate (W805-BBBB PIN #4). Confirms "
        "counterfactual-axis FAMILY across two commands. Separate fix wave."
    ),
)
def test_resolution_field_present_on_success(cli_runner, tiny_corpus):
    """Pin (Pattern-1-V-D shape): success envelope must disclose how the
    partition was achieved (direct / merged / split / bisection_fallback)."""
    result = _invoke_orchestrate(cli_runner, tiny_corpus, "--agents", "2", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    partition_method = (
        summary.get("partition_method") or summary.get("resolution") or summary.get("partition_resolution")
    )
    assert partition_method in {
        "direct",
        "merged",
        "split",
        "bisection_fallback",
        "louvain_direct",
        "louvain_adjusted",
    }, (
        f"success envelope must disclose partition resolution path; got "
        f"partition_method={partition_method!r}. summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Sister cross-check: W805-BBBB invariants preserved
# ---------------------------------------------------------------------------


class TestW805BBBBSisterParityPreserved:
    """Confirm W805-BBBB cmd_simulate sealed-today behaviour still holds.

    Run isolation parity: this file must not change cmd_simulate behaviour.
    """

    def test_simulate_module_importable(self):
        """Smoke: cmd_simulate module still imports cleanly."""
        from roam.commands import cmd_simulate

        assert hasattr(cmd_simulate, "simulate") or hasattr(cmd_simulate, "simulate_group"), (
            "cmd_simulate must still expose its click group"
        )

    def test_partition_module_unchanged_surface(self):
        """graph/partition.py exposes partition_for_agents + the helpers
        referenced by the pin reasons (regression guard for accidental rename)."""
        from roam.graph import partition

        assert callable(partition.partition_for_agents)
        assert callable(partition._empty_result)
        assert callable(partition._adjust_cluster_count)
