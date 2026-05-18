"""W805-U - empty-corpus Pattern-2 smoke for ``roam orchestrate`` (W805 sweep).

Twenty-first-in-batch of the W805 sweep. cmd_orchestrate is a multi-signal
partitioning compound that composes Louvain clustering + cluster-count
adjustment + agent-descriptor building + shared-interface detection +
conflict-probability + merge-order. Peer shape to cmd_adversarial (W805-R hit
4 disclosure gaps) and cmd_fan / cmd_uses. If the command silently emits a
verdict like "orchestrated N agents with 0 write conflicts across 0 shared
interfaces" on a corpus where every signal came from ``_empty_result(n_agents)``
- N stub agents with ``cluster_label="empty-N"`` and 0 symbols owned - the
agent reading the verdict will proceed under a false-partition signal.

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json orchestrate --agents 3`` on:

1. An empty-file corpus (``empty.py`` with no symbols)
2. A 1-symbol corpus (isolated ``isolated_fn`` with 0 edges, no callers)
3. A no-match ``--file`` filter (``--file nonexistent_dir``)
4. A populated 2-symbol corpus with 1 edge (regression baseline)

Empty + isolated probes both returned:

    summary.partial_success         : false
    summary.verdict                 : "orchestrated 3 agents with 0 write
                                       conflicts across 0 shared interfaces"
    summary.state                   : MISSING (no state key on the envelope)
    agents[*].cluster_label         : "empty-1", "empty-2", "empty-3" (signal
                                       that _empty_result fired, but never
                                       surfaced to verdict / partial_success /
                                       state)
    agents[*].symbols_owned         : 0 across all agents (empty corpus) or
                                       [0, 1, 0] (isolated corpus - one agent
                                       owns the single isolated symbol, the
                                       other two own nothing)

The no-match ``--file nonexistent`` path IS correctly disclosed - it emits
``verdict: "No matching files found"`` (cmd_orchestrate.py L137). That branch
proves the author knew how to disclose no-data; the gap is on the
``_empty_result`` cascade through ``partition_for_agents``.

W978 findings: 4 Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **CRITICAL: verdict ``"orchestrated N agents with 0 write conflicts ..."``
   cascades from ``_empty_result(n_agents)``** (``cmd_orchestrate.py`` L176-179,
   ``graph/partition.py`` L46-47 + L162-181). When ``len(G) == 0`` the
   partition engine short-circuits to ``_empty_result`` which returns N stub
   agents with ``cluster_label="empty-N"``, 0 symbols owned, 0 contracts. The
   verdict-builder reads ``len(agents)``, ``write_conflicts``, and
   ``len(shared_interfaces)`` from this stub result and emits the canonical
   "orchestrated N agents" verdict - indistinguishable from a real partition
   that found N work zones and decided they had 0 conflicts. Agents reading
   the verdict proceed under a false-partition signal.

2. **HIGH: ``summary.partial_success=False`` on a no-data corpus**
   (``cmd_orchestrate.py`` envelope at L201-209 omits the field; auto-derive
   stamps it to False). Same shape as W805-L #4 (preflight cascade) and
   W805-R #3 (adversarial cascade). When the partition engine's _empty_result
   stubs N agents, ``partial_success`` defaults to False because no exception
   was raised. Fix template: when ``_empty_result`` fired (or detect via
   ``all(a["cluster_label"].startswith("empty-") for a in agents)``), set
   ``partial_success=True`` + ``state="insufficient_signal_data"``.

3. **HIGH: ``summary.state`` MISSING entirely from the envelope**
   (``cmd_orchestrate.py`` L201-209). Peer commands (cmd_adversarial,
   cmd_preflight, cmd_diagnose) carry a closed-enum ``state`` field whose
   purpose is exactly this: disclose post-run conditions like
   "all_checks_ran" / "partial_*" / "insufficient_input". cmd_orchestrate has
   NO state field at all - agents have no machine-readable way to distinguish
   "real partition with N agents" from "stub partition because graph was
   empty". Fix template: add ``summary.state`` with closed enum
   ``{partition_emitted, no_data_in_corpus, no_matching_files}`` and emit
   ``no_data_in_corpus`` when ``_empty_result`` fired.

4. **MEDIUM: ``agent_contract.facts[0]`` mirrors the silent-partition verdict**
   (``cmd_orchestrate.py`` L187-189). LAW 4 anchoring is correct (``agents``
   / ``conflicts`` / ``interfaces``) but the fact itself is a lie when the
   corpus had nothing to partition. Cascade from #1 - sealing #1 seals this
   as a side effect.

The clean-corpus path is verified by the
``test_clean_corpus_emits_real_partition`` positive coverage below.

This wave pins #1-#3 via xfail-strict. #4 is a downstream cascade.

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_u_cmd_orchestrate_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_orchestrate*.py -x -n 0
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
    """Initialize a git repo and commit the baseline files."""
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
    """Invoke ``roam orchestrate`` via the Click group so the top-level
    ``--json`` flag is honoured by ``ctx.obj``.
    """
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
    """Indexed project with a single empty .py file.

    Indexer runs cleanly and extracts 0 function/class/method symbols.
    ``build_symbol_graph`` returns a 0-node graph. ``partition_for_agents``
    short-circuits to ``_empty_result(n_agents)`` which returns N stub
    agents with ``cluster_label="empty-N"`` - but the cmd_orchestrate
    envelope never surfaces that "this is a stub partition" state.
    """
    proj = tmp_path / "empty_orch_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def isolated_symbol_corpus(tmp_path, monkeypatch):
    """Indexed project with 1 file containing 1 isolated function.

    The function has 0 incoming edges, 0 outgoing edges, is not in any
    cluster (Louvain needs edges), no shared interfaces, no callers. The
    graph has 1 node + 0 edges. Pattern-2 probe: a single isolated symbol
    cannot meaningfully partition into 3 agents - one agent will own the
    symbol, two will be stubs. The envelope should disclose this trivial
    state but currently does not.
    """
    proj = tmp_path / "isolated_orch_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "lone.py").write_text(
        "def isolated_fn():\n    return 1\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def no_match_file_corpus(tmp_path, monkeypatch):
    """Indexed project where --file points to a path that doesn't match.

    Probes the ``target_files == []`` early-return branch
    (cmd_orchestrate.py L136-158) which IS correctly disclosed today via
    the explicit "No matching files found" verdict. This fixture serves
    as a *positive* sealed-today control showing the author already knew
    how to disclose no-data on at least one branch - which makes the
    _empty_result cascade gap clearly an oversight rather than a design
    choice.
    """
    proj = tmp_path / "nomatch_orch_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols + edges - regression baseline.

    Two functions where ``caller`` calls ``helper`` - exactly 1 edge.
    partition_for_agents has real graph data to operate on. The verdict
    should be a real partition emission, NOT routed through _empty_result.
    """
    proj = tmp_path / "clean_orch_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestOrchestrateEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_orchestrate envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam orchestrate --agents 3`` on empty corpus exits 0, no crash."""
        result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=orchestrate`` + non-empty verdict."""
        result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "orchestrate"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, non-placeholder)."""
        result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_envelope_required_fields(self, cli_runner, empty_corpus):
        """Envelope carries the canonical summary fields cmd_orchestrate emits."""
        result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        for field in (
            "verdict",
            "n_agents",
            "write_conflicts",
            "shared_interfaces_count",
            "conflict_probability",
        ):
            assert field in summary, f"empty envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # Top-level lists are present (possibly empty).
        for field in ("agents", "merge_order", "shared_interfaces"):
            assert field in envelope, f"envelope missing top-level {field!r}; got {sorted(envelope.keys())}"

    def test_no_match_file_filter_discloses_no_data(self, cli_runner, no_match_file_corpus):
        """Positive sealed-today control: the --file no-match branch IS
        correctly disclosed today via 'No matching files found'.

        This confirms the author knew how to surface a no-data state on at
        least one branch. The _empty_result cascade gap (pin #1) is the
        complementary oversight on the other branch.
        """
        result = _invoke_orchestrate(
            cli_runner,
            no_match_file_corpus,
            "--agents",
            "3",
            "--file",
            "nonexistent_directory_xyz",
            json_mode=True,
        )
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"].lower()
        assert "no matching" in verdict or "no files" in verdict, (
            f"expected --file no-match to disclose no-data; got {envelope['summary']['verdict']!r}"
        )

    def test_clean_corpus_emits_real_partition(self, cli_runner, clean_corpus):
        """Happy-path positive coverage: a populated corpus emits a real envelope.

        Every signal slot must be present, the agents must own real
        symbols (NOT all be cluster_label='empty-N' stubs), and the
        verdict must reflect a real partition.
        """
        result = _invoke_orchestrate(cli_runner, clean_corpus, "--agents", "2", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Canonical summary fields all present.
        for field in (
            "verdict",
            "n_agents",
            "write_conflicts",
            "shared_interfaces_count",
            "conflict_probability",
        ):
            assert field in summary, f"clean envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # On a clean corpus, at least one agent must own real symbols
        # (NOT all be cluster_label='empty-N' stubs).
        agents = envelope["agents"]
        assert len(agents) == 2
        total_symbols = sum(a["symbols_owned"] for a in agents)
        assert total_symbols >= 1, (
            f"clean corpus must produce at least 1 symbol owned across all "
            f"agents; got {total_symbols}. Agents: {agents!r}"
        )
        # Not every agent should be the 'empty-N' stub on a clean corpus
        # with 2 symbols.
        non_empty = [a for a in agents if not str(a.get("cluster_label", "")).startswith("empty-")]
        assert len(non_empty) >= 1, (
            f"clean corpus: at least 1 agent must have a real cluster_label "
            f"(non 'empty-N'); got {[a['cluster_label'] for a in agents]!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - CRITICAL: silent "orchestrated N agents" on empty corpus
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-U REAL BUG (CRITICAL): cmd_orchestrate.py L176-179 emits "
        "'orchestrated 3 agents with 0 write conflicts across 0 shared "
        "interfaces' when the graph has 0 nodes and partition_for_agents "
        "short-circuited to _empty_result(n_agents). The 3 stub agents "
        "have cluster_label='empty-N' which is internal signal that the "
        "stub path fired - but that signal is never lifted into the "
        "verdict. An agent reading 'orchestrated 3 agents with 0 write "
        "conflicts' proceeds under a false-partition signal, "
        "indistinguishable from a real partition where 3 agents each "
        "have non-trivial work zones and decided no edges crossed them. "
        "Fix template: at cmd_orchestrate.py before building the verdict, "
        'detect \'all(a["cluster_label"].startswith("empty-") for a in '
        "agents)' and emit verdict='no signal in indexed corpus (0 "
        "symbols, 0 edges)' OR similar. Reference: graph/partition.py "
        "L46-47 (the len(G)==0 short-circuit) + L162-181 "
        "(_empty_result). Separate fix wave."
    ),
)
def test_no_silent_partition_success_on_empty(cli_runner, empty_corpus):
    """Pin: empty corpus must NOT emit the canonical partition verdict.

    The cascade: ``empty.py`` indexes to 0 symbols -> build_symbol_graph
    returns 0-node graph -> partition_for_agents short-circuits to
    _empty_result(3) which returns 3 stub agents -> cmd_orchestrate
    builds verdict from len(agents)=3 + write_conflicts=0 +
    len(shared_interfaces)=0 -> emits 'orchestrated 3 agents with 0
    write conflicts across 0 shared interfaces' - indistinguishable
    from a real partition.
    """
    result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    verdict = envelope["summary"]["verdict"].lower()
    # The bug: verdict claims orchestration succeeded with N real agents.
    # The fix: verdict must surface that this is a stub / no-data path.
    success_markers = ("orchestrated 3 agents", "orchestrated 3 ")
    no_data_markers = (
        "no signal",
        "no data",
        "no symbols",
        "no indexed",
        "empty corpus",
        "empty graph",
        "insufficient",
    )
    looks_successful = any(m in verdict for m in success_markers)
    discloses_no_data = any(m in verdict for m in no_data_markers)
    assert discloses_no_data and not looks_successful, (
        f"empty corpus must NOT emit a partition-success verdict; got "
        f"{envelope['summary']['verdict']!r}. The 3 stub agents in "
        f"agents[*] have cluster_label='empty-N' (proof of "
        f"_empty_result path) but verdict claims real orchestration. "
        f"Agents reading this proceed under a false-partition signal."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: summary.partial_success=False on no-data corpus
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-U REAL BUG (HIGH): cmd_orchestrate.py L201-209 builds the "
        "summary dict without setting partial_success, so the "
        "json_envelope auto-derive defaults it to False. On the empty "
        "corpus (0 symbols, 0 edges), partition_for_agents falls through "
        "to _empty_result which returns 3 stub agents - no exception was "
        "raised, so partial_success stays False. Same cascading silent "
        "fallback as W805-L #4 (preflight cascade), W805-R #3 "
        "(adversarial cascade), W805-T #2 (uses cascade). Fix template: "
        'when _empty_result fired (detect via \'all(a["cluster_label"]'
        '.startswith("empty-") for a in agents)\'), set '
        "partial_success=True + state='insufficient_signal_data'. "
        "Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """Pin: when _empty_result stubbed the agents, partial_success=True.

    The empty corpus has 0 symbols and 0 edges. partition_for_agents
    short-circuits to _empty_result. partial_success=False here is
    the canonical Pattern-2 cascade - there's no real partition to
    certify success from.
    """
    result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty corpus has 0 symbols + 0 edges and _empty_result stubbed "
        f"all 3 agents (cluster_label='empty-N'); partial_success MUST be "
        f"True. Got summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - HIGH: summary.state MISSING from cmd_orchestrate envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-U REAL BUG (HIGH): cmd_orchestrate.py L201-209 builds "
        "summary={verdict, n_agents, write_conflicts, "
        "shared_interfaces_count, conflict_probability} - NO state field. "
        "Peer multi-signal commands (cmd_adversarial, cmd_preflight, "
        "cmd_diagnose) carry a closed-enum 'state' field whose purpose is "
        "exactly this: disclose post-run conditions like 'all_checks_ran' "
        "/ 'partial_*' / 'insufficient_input'. cmd_orchestrate has NO "
        "state field at all - agents have no machine-readable way to "
        "distinguish 'real partition with N agents' from 'stub partition "
        "from _empty_result'. Fix template: add summary.state with closed "
        "enum {partition_emitted, no_data_in_corpus, no_matching_files} "
        "and emit 'no_data_in_corpus' when _empty_result fired. Separate "
        "fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """Pin: summary.state must distinguish 'real partition' from 'stub partition'.

    The current envelope has no state field at all - agents must parse
    the verdict string to guess at the run state.
    """
    result = _invoke_orchestrate(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    state = envelope["summary"].get("state")
    assert state in {
        "no_data_in_corpus",
        "insufficient_signal_data",
        "no_signal_data",
        "empty_input",
        "stub_partition",
    }, (
        f"empty corpus summary.state must disclose the stub-partition "
        f"state; got {state!r}. cmd_orchestrate has no state field at "
        f"all today - agents reading 'orchestrated 3 agents' cannot "
        f"distinguish a real partition from a _empty_result cascade."
    )


# ---------------------------------------------------------------------------
# Multi-signal level disclosure: signal-level explicit no-data
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-U REAL BUG (HIGH, multi-signal): cmd_orchestrate composes 4 "
        "signals (agents, merge_order, shared_interfaces, "
        "write_conflicts/conflict_probability) but the envelope reports "
        "each as a bare count without disclosing whether the underlying "
        "computation had real graph input. On the isolated_symbol_corpus "
        "(1 node + 0 edges), Louvain returns 0 clusters (no edges to "
        "modularize), _find_shared_interfaces returns [] (no cross-"
        "partition edges to share), compute_conflict_probability returns "
        "0.0 (len(G.edges)==0 branch). The envelope reports all 4 as 0 - "
        "indistinguishable from 'real partition, 4 clean signals'. Fix "
        "template: stamp a per-signal '_definition' or 'state' sidecar "
        "(e.g. shared_interfaces_state='no_edges_to_share', "
        "conflict_probability_state='no_edges_in_graph'). Separate fix "
        "wave."
    ),
)
def test_signal_level_explicit_no_data(cli_runner, isolated_symbol_corpus):
    """Pin: each multi-signal slot must disclose ran-with-data vs ran-on-empty.

    On the isolated_symbol_corpus, the graph has 1 node + 0 edges. Each
    of the 4 signal computations short-circuits on empty input:
      - clusters: Louvain returns 0 (no edges to modularize)
      - shared_interfaces: 0 (no cross-partition edges)
      - conflict_probability: 0.0 (len(G.edges)==0 branch in
        compute_conflict_probability)
      - write_conflicts: 0 (no duplicate write files)

    The envelope must distinguish 'real signal returned 0' from
    'signal had no input'.
    """
    result = _invoke_orchestrate(cli_runner, isolated_symbol_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    # At least one of the 4 signal slots must disclose its no-data state.
    sidecar_keys = (
        "shared_interfaces_state",
        "conflict_probability_state",
        "write_conflicts_state",
        "agents_state",
        "shared_interfaces_definition",
        "conflict_probability_definition",
    )
    no_data_disclosed = any(
        isinstance(summary.get(k), str)
        and (
            "no_edges" in summary[k]
            or "no_data" in summary[k]
            or "no_input" in summary[k]
            or "empty" in summary[k]
            or "insufficient" in summary[k]
        )
        for k in sidecar_keys
    )
    assert no_data_disclosed, (
        f"isolated_symbol_corpus has 0 edges; each multi-signal slot "
        f"computed 0 from empty input. At least one '_state' or "
        f"'_definition' sidecar must disclose the no-data state. Got "
        f"summary keys={sorted(summary.keys())!r}. Agents reading "
        f"'write_conflicts=0 / shared_interfaces_count=0 / "
        f"conflict_probability=0.0' cannot distinguish 'real signals, "
        f"all 0' from 'no-input cascade'."
    )
