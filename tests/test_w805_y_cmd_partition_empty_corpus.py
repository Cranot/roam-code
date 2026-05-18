"""W805-Y - empty-corpus Pattern-2 smoke for ``roam partition`` (W805 sweep).

Twenty-fifth-in-batch of the W805 sweep. cmd_partition is the named sibling
of cmd_orchestrate (W805-U, 4 REAL BUGS) — both produce multi-agent
partition manifests, both compose Louvain clustering + cluster-count
adjustment + per-agent metrics + conflict-probability + merge-order.

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json partition --agents 3`` on:

1. An empty-file corpus (``empty.py`` with no symbols).
2. A 1-symbol corpus (isolated ``isolated_fn`` with 0 edges, no callers).
3. A populated 2-symbol corpus with 1 edge (regression baseline).

Empty-corpus probe:
- ``cmd_partition.compute_partition_manifest`` detects ``len(G) == 0`` at
  L383 and short-circuits to its OWN ``_empty_manifest(n_agents)`` (L558-569).
  This is a DIFFERENT engine path than cmd_orchestrate's ``_empty_result``
  (cmd_orchestrate uses ``partition_for_agents`` from ``graph/partition.py``;
  cmd_partition rolls its own ``compute_partition_manifest`` and never calls
  ``partition_for_agents``).
- The empty-manifest verdict already includes the literal ``"0 partitions"``
  — partial good-citizen behaviour the cmd_orchestrate path lacks. BUT:
  ``summary.partial_success=False`` (Pattern-2 silent fallback) and there's
  no ``summary.state`` field at all (consistent gap with W805-U pin #3).

Isolated-symbol probe (1 node + 0 edges, --agents 3):
- ``compute_partition_manifest`` runs the full pipeline. Louvain on 1 node
  returns 1 cluster. ``_adjust_cluster_count(G, groups, 3)`` pads to 3
  partitions by splitting — 2 of which end up with ``symbol_count=0``,
  ``file_count=0``, ``files=[]``, ``role="General Module"``. The verdict
  reads "conflict probability 0% across 3 partitions for 3 agents" —
  indistinguishable from a real 3-cluster decomposition where each agent
  owns real work. Same Pattern-2 disclosure failure shape as W805-U pin #1,
  DIFFERENT mechanism (``_adjust_cluster_count`` stub-padding here vs
  ``_empty_result`` cascade there).

W978 findings: Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **HIGH: ``summary.partial_success=False`` on empty-corpus _empty_manifest
   path** (``cmd_partition.py`` envelope at L789-814; ``_empty_manifest`` at
   L558-569 omits the field; auto-derive stamps it to False). Same shape as
   W805-U #2. Empty graph -> ``_empty_manifest`` -> envelope reports 0
   partitions but ``partial_success=False`` — no exception was raised so
   auto-derive defaults to False. Fix template: ``_empty_manifest`` should
   carry ``partial_success=True`` and the envelope should lift it.

2. **HIGH: ``summary.state`` MISSING entirely from the envelope**
   (``cmd_partition.py`` L789-814). Same shape as W805-U #3. cmd_partition
   has no machine-readable state slot — peer commands carry a closed-enum
   ``state`` field. Agents reading "0 partitions" must parse the verdict
   string instead of reading ``state="no_data_in_corpus"``.

3. **CRITICAL: 1-symbol isolated corpus emits verdict claiming "3 partitions
   for 3 agents"** (``cmd_partition.py`` L541-544; cascade through
   ``_adjust_cluster_count`` in ``graph/partition.py``). On a graph with 1
   node + 0 edges, ``compute_partition_manifest`` runs the full pipeline and
   ``_adjust_cluster_count(G, groups, 3)`` pads the single real cluster with
   2 empty stub partitions (``symbol_count=0`` / ``file_count=0`` /
   ``files=[]``). The verdict counts ALL 3 in "3 partitions for 3 agents"
   without disclosing that 2 are stubs. Agents reading the verdict proceed
   under a false-partition signal. DIFFERENT engine path from W805-U
   (``_adjust_cluster_count`` padding vs ``_empty_result`` cascade) but
   SAME Pattern-2 disclosure failure shape. Fix template: when
   ``sum(1 for p in result_partitions if p["symbol_count"]==0) > 0``,
   surface the stub count in the verdict (e.g. "1 real + 2 stub partitions
   for 3 agents") + set ``partial_success=True`` + state="overprovisioned".

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_y_cmd_partition_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_partition.py -x -n 0
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


def _invoke_partition(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam partition`` via the Click group so the top-level
    ``--json`` flag is honoured by ``ctx.obj``.
    """
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("partition")
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
    ``build_symbol_graph`` returns a 0-node graph. cmd_partition's
    ``compute_partition_manifest`` detects ``len(G)==0`` and short-circuits
    to its OWN ``_empty_manifest(n_agents)`` (NOT through partition_for_agents).
    """
    proj = tmp_path / "empty_part_corpus"
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

    The function has 0 incoming/outgoing edges. Graph has 1 node + 0 edges.
    ``compute_partition_manifest`` runs the full pipeline (does NOT short-
    circuit since len(G)==1, not 0). ``_adjust_cluster_count`` pads to 3
    partitions, 2 of which end up as empty stubs (symbol_count=0).
    """
    proj = tmp_path / "isolated_part_corpus"
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
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols + edges - regression baseline.

    Two functions where ``caller`` calls ``helper`` - exactly 1 edge.
    ``compute_partition_manifest`` has real graph data to operate on.
    The verdict should be a real partition emission with non-stub
    partitions.
    """
    proj = tmp_path / "clean_part_corpus"
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


class TestPartitionEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_partition envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam partition --agents 3`` on empty corpus exits 0, no crash."""
        result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=partition`` + non-empty verdict."""
        result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "partition"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, non-placeholder)."""
        result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_envelope_required_fields(self, cli_runner, empty_corpus):
        """Envelope carries the canonical summary fields cmd_partition emits."""
        result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        for field in (
            "verdict",
            "n_agents",
            "total_partitions",
            "overall_conflict_probability",
        ):
            assert field in summary, f"empty envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # Top-level lists are present (empty on empty corpus).
        for field in ("partitions", "dependencies", "conflict_hotspots", "merge_order"):
            assert field in envelope, f"envelope missing top-level {field!r}; got {sorted(envelope.keys())}"

    def test_empty_corpus_verdict_says_zero_partitions(self, cli_runner, empty_corpus):
        """Empty-corpus verdict explicitly includes ``0 partitions`` count.

        cmd_partition's ``_empty_manifest`` deserves partial credit: unlike
        cmd_orchestrate's ``_empty_result`` cascade (W805-U pin #1) which
        emits ``"orchestrated 3 agents"`` indistinguishable from a real
        partition, cmd_partition's verdict already shows ``"0 partitions"``.
        Lock that in as sealed-today good-citizen behaviour.
        """
        result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"].lower()
        assert "0 partitions" in verdict, (
            f"empty corpus verdict must explicitly say '0 partitions'; got {envelope['summary']['verdict']!r}"
        )
        assert envelope["summary"]["total_partitions"] == 0
        assert envelope["partitions"] == []

    def test_clean_corpus_emits_real_partition(self, cli_runner, clean_corpus):
        """Happy-path positive coverage: a populated corpus emits a real envelope.

        Every signal slot must be present, at least one partition must own
        real symbols (NOT all be empty stubs), and the verdict must reflect
        a real partition.
        """
        result = _invoke_partition(cli_runner, clean_corpus, "--agents", "2", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        # Canonical summary fields all present.
        for field in (
            "verdict",
            "n_agents",
            "total_partitions",
            "overall_conflict_probability",
        ):
            assert field in summary, f"clean envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        partitions = envelope["partitions"]
        assert len(partitions) == 2
        total_symbols = sum(p["symbol_count"] for p in partitions)
        assert total_symbols >= 1, (
            f"clean corpus must produce at least 1 symbol owned across all "
            f"partitions; got {total_symbols}. Partitions: {partitions!r}"
        )
        # On a clean corpus with 2 real symbols, not every partition should
        # be an empty stub (symbol_count=0).
        non_stub = [p for p in partitions if p["symbol_count"] > 0]
        assert len(non_stub) >= 1, (
            f"clean corpus: at least 1 partition must own real symbols; got "
            f"{[(p['id'], p['symbol_count']) for p in partitions]!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - HIGH: partial_success=False on empty-corpus cascade
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Y REAL BUG (HIGH): cmd_partition.py L789-814 builds the "
        "summary dict without setting partial_success, so the "
        "json_envelope auto-derive defaults it to False. On the empty "
        "corpus (0 symbols, 0 edges), compute_partition_manifest detects "
        "len(G)==0 at L383 and short-circuits to _empty_manifest "
        "(L558-569) — no exception was raised, so partial_success stays "
        "False. Same cascading silent fallback as W805-U #2 (orchestrate "
        "cascade), W805-L #4 (preflight cascade), W805-R #3 (adversarial "
        "cascade), W805-T #2 (uses cascade). Fix template: "
        "_empty_manifest should disclose its stub state via the envelope "
        "(set partial_success=True + state='no_data_in_corpus'). "
        "Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """Pin: when _empty_manifest fired, partial_success=True.

    The empty corpus has 0 symbols and 0 edges. compute_partition_manifest
    short-circuits to _empty_manifest. partial_success=False here is
    the canonical Pattern-2 cascade - there's no real partition to
    certify success from.
    """
    result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty corpus has 0 symbols + 0 edges and _empty_manifest fired "
        f"(total_partitions=0); partial_success MUST be True. Got "
        f"summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: summary.state MISSING from cmd_partition envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Y REAL BUG (HIGH): cmd_partition.py L789-814 builds "
        "summary={verdict, n_agents, total_partitions, "
        "overall_conflict_probability, complexity_definition} - NO state "
        "field. Peer multi-signal commands (cmd_adversarial, cmd_preflight, "
        "cmd_diagnose) carry a closed-enum 'state' field whose purpose is "
        "exactly this: disclose post-run conditions like 'all_checks_ran' "
        "/ 'partial_*' / 'insufficient_input'. cmd_partition has NO state "
        "field at all - agents have no machine-readable way to distinguish "
        "'real partition with 0 partitions because corpus was non-trivial' "
        "from 'stub partition because graph was empty'. Same gap as "
        "W805-U #3 on the named-sibling cmd_orchestrate. Fix template: "
        "add summary.state with closed enum {partition_emitted, "
        "no_data_in_corpus, overprovisioned} and emit 'no_data_in_corpus' "
        "when _empty_manifest fired. Separate fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """Pin: summary.state must distinguish 'real partition' from 'stub partition'.

    The current envelope has no state field at all - agents must parse
    the verdict string to guess at the run state.
    """
    result = _invoke_partition(cli_runner, empty_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    state = envelope["summary"].get("state")
    assert state in {
        "no_data_in_corpus",
        "insufficient_signal_data",
        "no_signal_data",
        "empty_input",
        "stub_partition",
        "empty_manifest",
    }, (
        f"empty corpus summary.state must disclose the stub-partition "
        f"state; got {state!r}. cmd_partition has no state field at "
        f"all today - agents reading '0 partitions' cannot distinguish "
        f"a real 0-partition decomposition from an _empty_manifest cascade."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - CRITICAL: silent overprovisioning on isolated-symbol
# corpus (different engine path than W805-U, same disclosure shape)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-Y REAL BUG (CRITICAL): cmd_partition.py L541-544 emits "
        "'conflict probability 0% across 3 partitions for 3 agents' when "
        "the indexed corpus has 1 symbol and 0 edges. The cascade: "
        "compute_partition_manifest runs the full pipeline (does NOT "
        "short-circuit because len(G)==1, not 0) -> Louvain returns 1 "
        "cluster -> _adjust_cluster_count(G, groups, 3) from "
        "graph/partition.py PADS the single cluster to 3 partitions by "
        "splitting -> 2 of the 3 result_partitions end up with "
        "symbol_count=0, file_count=0, files=[]. The verdict counts ALL "
        "3 in '3 partitions for 3 agents' without disclosing that 2 are "
        "empty stubs. Agents reading 'partitioned into 3 clusters' "
        "(agent_contract.facts[1]) and assigning 3 workers to the result "
        "will dispatch 2 workers to empty work zones. Different engine "
        "path than W805-U (which used _empty_result cascade), SAME "
        "Pattern-2 disclosure failure shape - the verdict-builder reads "
        "len(result_partitions) without filtering stubs. Fix template: "
        "at cmd_partition.py before building the verdict, compute "
        "real_partitions = [p for p in result_partitions if "
        "p['symbol_count'] > 0]; if len(real_partitions) < n_agents, "
        "surface the stub count in the verdict (e.g. "
        "'1 real + 2 stub partitions for 3 agents') + set "
        "partial_success=True + state='overprovisioned'. Separate fix "
        "wave."
    ),
)
def test_no_silent_overprovisioned_partition(cli_runner, isolated_symbol_corpus):
    """Pin: 1-symbol corpus must NOT silently claim '3 partitions' when 2 are stubs.

    The isolated_symbol_corpus has exactly 1 indexed function with 0 edges.
    Asking for 3 agents forces _adjust_cluster_count to pad with 2 empty
    stub partitions. The verdict claims '3 partitions for 3 agents' —
    indistinguishable from a real 3-cluster decomposition.
    """
    result = _invoke_partition(cli_runner, isolated_symbol_corpus, "--agents", "3", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    partitions = envelope["partitions"]

    # Verify the bug shape: total_partitions=3 but >=1 partitions have
    # symbol_count=0 (stub padding from _adjust_cluster_count).
    stub_count = sum(1 for p in partitions if p["symbol_count"] == 0)
    real_count = sum(1 for p in partitions if p["symbol_count"] > 0)

    # The fix should disclose the stub-padding state via one of:
    #  - verdict mentions "stub" / "real" / "overprovisioned" / "empty"
    #  - summary.state explicitly says "overprovisioned" / "stub_partition"
    #  - summary.partial_success=True
    verdict = summary["verdict"].lower()
    state = summary.get("state", "")
    discloses_stubs = (
        any(
            m in verdict
            for m in (
                "stub",
                "real",
                "overprovision",
                "empty partition",
                "of 3 partitions",
                " stub ",
                "no signal",
                "no data",
            )
        )
        or "overprovisioned" in str(state)
        or "stub" in str(state)
        or summary.get("partial_success") is True
    )

    assert stub_count >= 1, (
        f"PROOF-OF-CASCADE: this test expects _adjust_cluster_count to pad "
        f"the 1-symbol corpus into 3 partitions with >=1 stub. Got "
        f"stub_count={stub_count}, real_count={real_count}, "
        f"partitions={partitions!r}. If this changed, re-evaluate the "
        f"cascade — the upstream engine may have been fixed."
    )
    assert discloses_stubs, (
        f"isolated_symbol_corpus has 1 real symbol -> _adjust_cluster_count "
        f"padded to 3 partitions where {stub_count} are stubs (symbol_count=0). "
        f"Verdict {summary['verdict']!r} must disclose this overprovisioning "
        f"OR summary.state must say 'overprovisioned' OR partial_success=True. "
        f"Today: verdict={summary['verdict']!r}, state={state!r}, "
        f"partial_success={summary.get('partial_success')!r}. Agents "
        f"reading 'partitioned into 3 clusters' will dispatch 3 workers "
        f"when only {real_count} have real work."
    )
