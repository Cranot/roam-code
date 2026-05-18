"""W805-BB - empty-corpus Pattern-2 smoke for ``roam clusters`` (W805 sweep).

Twenty-eighth-in-batch of the W805 sweep. Completes the architectural-
analysis triad: cmd_orchestrate (W805-U, 4 REAL BUGS) + cmd_partition
(W805-Y, 3 REAL BUGS) + cmd_clusters (this file). All three consume the
same Louvain community-detection engine (``graph/clusters.py``) and the
same ``build_symbol_graph`` upstream.

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json clusters`` on:

1. An empty-file corpus (``empty.py`` with no symbols).
2. A 1-symbol corpus (isolated ``isolated_fn``, --min-size 1 to surface it).
3. A populated 2-symbol corpus with 1 edge (regression baseline).

Empty-corpus probe:
- ``detect_clusters`` in ``graph/clusters.py`` L66 detects ``len(G) == 0``
  and returns ``{}``. The indexer never populates the ``clusters`` table.
- ``cmd_clusters.clusters`` (L346) reads ``ALL_CLUSTERS`` -> empty rows;
  ``build_symbol_graph`` -> 0-node graph; ``cluster_quality(G, {})`` returns
  ``{"modularity": 0.0, "per_cluster": {}, "mean_conductance": 0.0}``.
- The verdict-builder (L252-258) hits ``no clusters detected`` and the
  envelope is auto-populated with ``partial_success=False`` (no exception).
- This is a THIRD engine path in the W805 triad: cmd_clusters routes
  through ``detect_clusters`` -> empty mapping -> empty rows (vs
  cmd_partition's ``_empty_manifest`` short-circuit, vs cmd_orchestrate's
  ``_empty_result`` cascade). SAME Pattern-2 disclosure failure shape.

Isolated-symbol probe (1 node + 0 edges, --min-size 1):
- ``detect_clusters`` runs Louvain on the 1-node graph (does NOT short-
  circuit on len==1). Louvain returns 1 community of 1 node.
  ``cluster_quality`` returns modularity_q=0.0 because there is no
  community structure to measure on 1 node.
- The verdict-builder reads ``visible_pre`` (the 1-cluster row) and emits
  ``"1 clusters, largest: isolated_fn(1 syms)"`` -- indistinguishable
  from a real cluster decomposition. ``modularity_q=0.0`` (Newman 2004
  threshold Q>0.3 for "meaningful community structure") would tell an
  agent this is not a community, but the verdict alone hides it.

W978 findings: Pattern-2 disclosure gaps, ranked by agent-impact
============================================================

1. **HIGH: ``summary.partial_success=False`` on empty-corpus path**
   (``cmd_clusters.py`` L260-294 ``_clusters_json``; auto-derive defaults
   to False because no exception was raised). Same shape as
   W805-Y #1 (partition), W805-U #2 (orchestrate). Empty graph ->
   ``detect_clusters`` returns ``{}`` -> envelope reports 0 clusters but
   ``partial_success=False``. Fix template: when ``len(visible)==0``
   AND ``len(rows)==0``, set ``partial_success=True`` and
   ``state="no_data_in_corpus"``.

2. **HIGH: ``summary.state`` MISSING entirely from the envelope**
   (``cmd_clusters.py`` L262-268). Same shape as W805-Y #2, W805-U #3.
   cmd_clusters has no machine-readable state slot -- agents reading
   ``"no clusters detected"`` must parse the verdict string instead of
   reading ``state="no_data_in_corpus"`` or ``state="below_min_size"``.

3. **CRITICAL: 1-symbol isolated corpus with --min-size 1 emits verdict
   claiming "1 clusters, largest: isolated_fn(1 syms)"** with
   ``modularity_q=0.0`` (``cmd_clusters.py`` L254-258 + L370). On a
   graph with 1 node + 0 edges, Louvain returns 1 trivial community.
   The verdict counts this as a real cluster without disclosing the
   modularity Q=0.0 signal that says "no actual community structure
   detected" (Newman 2004). Agents reading "1 clusters" and proceeding
   to architectural-refactoring decisions are operating on a phantom
   cluster. SAME Pattern-2 disclosure failure shape as W805-Y #3 and
   W805-U #1. Fix template: when ``modularity_q <= 0.0`` (no structure
   detected) AND visible cluster count >= 1, surface the no-structure
   signal in the verdict (e.g. "1 trivial cluster, no community
   structure -- modularity 0.00") + set ``partial_success=True`` +
   ``state="trivial_clustering"``.

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_bb_cmd_clusters_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_clusters.py -x -n 0
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


def _invoke_clusters(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam clusters`` via the Click group so the top-level
    ``--json`` flag is honoured by ``ctx.obj``.
    """
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("clusters")
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
    ``build_symbol_graph`` returns a 0-node graph. ``detect_clusters``
    detects ``len(G)==0`` and returns ``{}``. The ``clusters`` table
    is never populated. The envelope falls through the auto-empty path.
    """
    proj = tmp_path / "empty_clu_corpus"
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
    ``detect_clusters`` runs Louvain (does NOT short-circuit since
    len(G)==1, not 0). Louvain returns 1 trivial community of 1 node.
    ``cluster_quality`` -> modularity_q=0.0 (no actual community
    structure to measure on 1 node). With ``--min-size 1`` the trivial
    cluster surfaces as visible and the verdict claims "1 clusters".
    """
    proj = tmp_path / "iso_clu_corpus"
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
    """Indexed project with 2 symbols + 1 edge - regression baseline.

    Two functions where ``caller`` calls ``helper`` - exactly 1 edge.
    With ``--min-size 1``, the resulting cluster surfaces with at least
    1 symbol.
    """
    proj = tmp_path / "clean_clu_corpus"
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


class TestClustersEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_clusters envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam clusters`` on empty corpus exits 0, no crash."""
        result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries ``command=clusters`` + non-empty verdict."""
        result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "clusters"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: verdict line stands alone (single line, non-placeholder)."""
        result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict", "OK", "ok"), f"verdict is a placeholder: {verdict!r}"

    def test_empty_corpus_envelope_required_fields(self, cli_runner, empty_corpus):
        """Envelope carries the canonical summary fields cmd_clusters emits."""
        result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        for field in (
            "verdict",
            "clusters",
            "mismatches",
            "modularity_q",
            "mean_conductance",
        ):
            assert field in summary, f"empty envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # Top-level lists are either present directly OR collapsed into
        # list_counts when stripped (default --no-detail mode).
        list_counts = envelope.get("list_counts", {})
        for field in ("clusters", "mismatches"):
            assert field in envelope or field in list_counts, (
                f"envelope missing top-level {field!r} (and not in "
                f"list_counts); got envelope keys={sorted(envelope.keys())}, "
                f"list_counts={list_counts!r}"
            )

    def test_empty_corpus_verdict_says_no_clusters(self, cli_runner, empty_corpus):
        """Empty-corpus verdict explicitly says 'no clusters detected'.

        cmd_clusters deserves partial credit for the explicit phrasing:
        the verdict literal includes "no clusters detected" rather than
        the more ambiguous "0 clusters". Lock this in as sealed-today
        good-citizen behaviour.
        """
        result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
        envelope = _parse_envelope(result)
        verdict = envelope["summary"]["verdict"].lower()
        assert "no clusters" in verdict, (
            f"empty corpus verdict must say 'no clusters detected'; got {envelope['summary']['verdict']!r}"
        )
        assert envelope["summary"]["clusters"] == 0
        # Lists may be stripped on no-detail; check via list_counts fallback.
        if "clusters" in envelope:
            assert envelope["clusters"] == []
        else:
            assert envelope.get("list_counts", {}).get("clusters", 0) == 0

    def test_clean_corpus_emits_real_clusters(self, cli_runner, clean_corpus):
        """Happy-path: a populated corpus emits a real envelope.

        Every signal slot must be present and at least 1 cluster must
        surface (with --min-size 1).
        """
        result = _invoke_clusters(cli_runner, clean_corpus, "--min-size", "1", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        summary = envelope["summary"]
        for field in (
            "verdict",
            "clusters",
            "mismatches",
            "modularity_q",
            "mean_conductance",
        ):
            assert field in summary, f"clean envelope missing summary field {field!r}; got {sorted(summary.keys())}"
        # Lists may be stripped (no-detail default) or present.
        if "clusters" in envelope:
            clusters = envelope["clusters"]
            assert len(clusters) >= 1, f"clean corpus must produce at least 1 visible cluster; got {clusters!r}"
            total_symbols = sum(c["size"] for c in clusters)
            assert total_symbols >= 1, (
                f"clean corpus must have at least 1 symbol owned across all "
                f"clusters; got {total_symbols}. Clusters: {clusters!r}"
            )
        else:
            # Stripped to list_counts only - check count.
            assert envelope.get("list_counts", {}).get("clusters", 0) >= 1, (
                f"clean corpus must produce at least 1 visible cluster; got list_counts={envelope.get('list_counts')!r}"
            )
        # summary.clusters always reflects the count regardless of strip.
        assert summary["clusters"] >= 1, f"summary.clusters must be >= 1 on clean corpus; got summary={summary!r}"


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #1 - HIGH: partial_success=False on empty-corpus path
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BB REAL BUG (HIGH): cmd_clusters.py L260-294 (_clusters_json) "
        "builds the summary dict without setting partial_success, so the "
        "json_envelope auto-derive defaults it to False. On the empty "
        "corpus (0 symbols, 0 edges), detect_clusters returns {} at "
        "graph/clusters.py L66 -- no exception was raised, so "
        "partial_success stays False. Same cascading silent fallback as "
        "W805-Y #1 (partition), W805-U #2 (orchestrate). All three commands "
        "consume the same graph/clusters.py engine. Fix template: when "
        "len(visible)==0 AND len(rows)==0, _clusters_json should "
        "disclose its empty state via the envelope (set "
        "partial_success=True + state='no_data_in_corpus'). "
        "Separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """Pin: when no clusters were detected, partial_success=True."""
    result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, (
        f"empty corpus has 0 symbols + 0 edges and detect_clusters "
        f"returned {{}} (clusters=0); partial_success MUST be True. Got "
        f"summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #2 - HIGH: summary.state MISSING from cmd_clusters envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BB REAL BUG (HIGH): cmd_clusters.py L262-268 builds "
        "summary={verdict, clusters, mismatches, modularity_q, "
        "mean_conductance} -- NO state field. Peer multi-signal commands "
        "(cmd_adversarial, cmd_preflight, cmd_diagnose) carry a closed-enum "
        "'state' field whose purpose is exactly this: disclose post-run "
        "conditions like 'no_data_in_corpus' / 'trivial_clustering' / "
        "'below_min_size'. cmd_clusters has NO state field at all -- "
        "agents have no machine-readable way to distinguish 'real cluster "
        "decomposition with 0 clusters because corpus was non-trivial' "
        "from 'no clusters because graph was empty' from 'clusters were "
        "below --min-size threshold'. Same gap as W805-Y #2, W805-U #3. "
        "Fix template: add summary.state with closed enum "
        "{clusters_detected, no_data_in_corpus, below_min_size, "
        "trivial_clustering} and emit the appropriate enum value. "
        "Separate fix wave."
    ),
)
def test_empty_corpus_state_explicit(cli_runner, empty_corpus):
    """Pin: summary.state must distinguish 'real decomposition' from 'empty'."""
    result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    state = envelope["summary"].get("state")
    assert state in {
        "no_data_in_corpus",
        "insufficient_signal_data",
        "empty_input",
        "no_clusters",
        "empty_graph",
        "no_signal_data",
    }, (
        f"empty corpus summary.state must disclose the empty-graph "
        f"state; got {state!r}. cmd_clusters has no state field at "
        f"all today -- agents reading 'no clusters detected' cannot "
        f"distinguish a real 0-cluster decomposition from an empty-"
        f"graph cascade vs a below-min-size filter."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - CRITICAL: silent trivial-cluster on isolated-symbol
# corpus (different engine path than W805-U/Y, same disclosure shape)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BB REAL BUG (CRITICAL): cmd_clusters.py L254-258 + L370 emit "
        "'1 clusters, largest: isolated_fn(1 syms)' when the indexed corpus "
        "has 1 symbol and 0 edges. The cascade: build_symbol_graph "
        "returns a 1-node graph -> detect_clusters runs Louvain (does "
        "NOT short-circuit since len(G)==1, not 0) -> Louvain returns 1 "
        "trivial community -> cluster_quality returns modularity_q=0.0 "
        "(no actual community structure on 1 node, Newman 2004 says "
        "Q>0.3 indicates real community structure) -> verdict-builder "
        "reads visible[0] and emits '1 clusters, largest: ...' "
        "indistinguishable from a real cluster decomposition. Agents "
        "reading agent_contract.facts[0]='1 clusters, largest: ...' "
        "and proceeding to architectural-refactoring decisions are "
        "operating on a phantom cluster. DIFFERENT engine path than "
        "W805-U (which used _empty_result cascade) and W805-Y (which "
        "used _adjust_cluster_count stub-padding), SAME Pattern-2 "
        "disclosure failure shape -- the verdict-builder reads "
        "len(visible) without filtering trivial clusters by modularity. "
        "Fix template: when modularity_q <= 0.0 AND visible cluster "
        "count >= 1, surface the no-structure signal in the verdict "
        "(e.g. '1 trivial cluster, modularity Q=0.00 -- no community "
        "structure detected') + set partial_success=True + "
        "state='trivial_clustering'. Separate fix wave."
    ),
)
def test_no_silent_well_modularized_on_empty(cli_runner, isolated_symbol_corpus):
    """Pin: 1-symbol corpus must NOT silently claim a real cluster when modularity_q=0.0.

    The isolated_symbol_corpus has exactly 1 indexed function with 0 edges.
    With ``--min-size 1`` to surface the trivial cluster, the verdict
    claims "1 clusters, largest: isolated_fn(1 syms)" while
    modularity_q=0.0 -- indistinguishable from a real well-modularized
    decomposition.
    """
    result = _invoke_clusters(cli_runner, isolated_symbol_corpus, "--min-size", "1", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope["summary"]
    clusters = envelope.get("clusters", [])

    # Verify the bug shape: clusters>=1 but modularity_q==0.0 (no structure).
    n_clusters = summary["clusters"]
    modularity_q = summary["modularity_q"]

    # The fix should disclose the trivial-cluster state via one of:
    #  - verdict mentions "trivial" / "no community structure" / "modularity"
    #  - summary.state explicitly says "trivial_clustering" / "no_structure"
    #  - summary.partial_success=True
    verdict = summary["verdict"].lower()
    state = summary.get("state", "")
    discloses_trivial = (
        any(
            m in verdict
            for m in (
                "trivial",
                "no community",
                "no structure",
                "modularity 0",
                "q=0",
                "phantom",
                "insufficient",
            )
        )
        or "trivial" in str(state)
        or "no_structure" in str(state)
        or "trivial_clustering" in str(state)
        or summary.get("partial_success") is True
    )

    assert n_clusters >= 1, (
        f"PROOF-OF-CASCADE: this test expects Louvain to return 1 trivial "
        f"cluster on the 1-symbol corpus with --min-size 1. Got "
        f"n_clusters={n_clusters}, modularity_q={modularity_q}, "
        f"clusters={clusters!r}. If this changed, re-evaluate the "
        f"cascade -- the upstream engine may have been fixed."
    )
    assert modularity_q == 0.0, (
        f"PROOF-OF-CASCADE: cluster_quality must report modularity_q=0.0 "
        f"on a 1-node graph (no community structure to measure). Got "
        f"modularity_q={modularity_q}. If this changed, re-evaluate."
    )
    assert discloses_trivial, (
        f"isolated_symbol_corpus has 1 real symbol -> Louvain returned 1 "
        f"trivial community -> modularity_q=0.0 (no community structure). "
        f"Verdict {summary['verdict']!r} must disclose this trivial-"
        f"cluster state OR summary.state must say 'trivial_clustering' "
        f"OR partial_success=True. Today: verdict={summary['verdict']!r}, "
        f"state={state!r}, partial_success={summary.get('partial_success')!r}, "
        f"modularity_q={modularity_q}. Agents reading '1 clusters' will "
        f"proceed under a false-cluster signal."
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #4 - HIGH: silent zero-clusters on empty (no disclosure
# distinguishing 'genuinely no clusters' from 'corpus was empty')
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BB REAL BUG (HIGH): cmd_clusters.py L258 emits "
        "verdict='no clusters detected' when detect_clusters returned {} "
        "because the graph was empty (0 symbols, 0 edges). An identical "
        "verdict would be emitted if the graph had real symbols but "
        "Louvain rejected every community as below quality threshold. "
        "The verdict + envelope cannot distinguish these two cases. "
        "agent_contract.facts[0]='no clusters detected' is a Pattern-2 "
        "silent fallback: the agent cannot tell whether to (a) re-run "
        "indexing, (b) lower --min-size, or (c) accept that there is no "
        "modular structure. Mirrors W805-U critical and W805-Y critical "
        "shape. Fix template: when len(rows)==0 AND the symbol corpus is "
        "also empty (n_symbols==0), the verdict should say 'no data in "
        "corpus -- no symbols to cluster' to disambiguate from 'symbols "
        "exist but Louvain found no community structure'. Separate fix wave."
    ),
)
def test_no_silent_zero_clusters_on_empty(cli_runner, empty_corpus):
    """Pin: empty corpus must distinguish 'no data' from 'no structure found'.

    The empty corpus has 0 symbols. The verdict "no clusters detected"
    is the same one that would surface if the corpus had real symbols
    but Louvain found no community structure. Agents must be able to
    tell the difference.
    """
    result = _invoke_clusters(cli_runner, empty_corpus, json_mode=True)
    envelope = _parse_envelope(result)
    verdict = envelope["summary"]["verdict"].lower()
    state = envelope["summary"].get("state", "")
    partial_success = envelope["summary"].get("partial_success")

    discloses_empty_corpus = (
        any(
            m in verdict
            for m in (
                "no symbols",
                "no data",
                "empty corpus",
                "empty graph",
                "0 symbols",
                "no data in corpus",
            )
        )
        or "no_data" in str(state)
        or "empty" in str(state)
        or partial_success is True
    )

    assert discloses_empty_corpus, (
        f"empty corpus (0 symbols, 0 edges) must disclose that the corpus "
        f"itself is empty -- 'no clusters detected' alone is indistinguishable "
        f"from a non-empty corpus where Louvain found no community structure. "
        f"Today: verdict={envelope['summary']['verdict']!r}, state={state!r}, "
        f"partial_success={partial_success!r}. Agents cannot tell whether "
        f"to re-run indexing, lower --min-size, or accept no modular structure."
    )
