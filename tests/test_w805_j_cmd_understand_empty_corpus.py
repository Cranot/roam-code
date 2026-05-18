"""W805-J - empty-corpus Pattern-2 smoke for ``roam understand`` (W805 sweep).

Tenth-in-batch of the W805 sweep, flagship 5-verb companion to W805-I
``cmd_describe``. ``roam understand`` is the headline "single-call codebase
comprehension" command listed under ``--help`` core verbs and the
``exploration`` capability category (``cmd_understand.py:662-679``).

Prior cohort yield (5 BUG-positive / 7 prior waves = 71%):

- A (cmd_owner)         REAL BUG - silent ``"top owner: ?"``
- B (cmd_minimap)       REAL BUG - silent ``"minimap rendered (148 chars)"``
- C (cmd_oracle)        REAL BUG - route-exists vs verdict mismatch
- D (cmd_workflow)      NO BUG - static metadata, wrong axis
- E (cmd_path_coverage) NO BUG - W807-hardened
- F (cmd_for_bug_fix)   REAL BUG - ``_compound_envelope`` aggregator (6 compounds)
- G (cmd_pr_prep)       REAL BUG - silent ``"READY"`` on no_changes children
- H (cmd_explain_command) in flight (NO BUG - static metadata)
- I (cmd_describe)      in flight
- J (cmd_understand)    this wave

W978 first-hypothesis re-run BEFORE writing any test
============================================================
Direct probe of ``roam --json understand`` on a 1-file / 0-symbol corpus
confirmed the hypothesis - **REAL BUG**. The flagship envelope emits:

    verdict        : 'healthy 1-lang project (100/100), 0 clusters, 1 hotspots'
    health_score   : 100
    partial_success: False
    state          : <ABSENT>
    symbols        : 0
    files          : 1
    clusters       : 0
    layers         : 0
    key_abs        : []
    entry_points   : []

The verdict says ``healthy ... (100/100)`` and ``partial_success=False``
while EVERY structural axis (symbols, clusters, layers, key_abstractions,
entry_points) is empty. ``hotspots`` reports 1 hit because the empty
``empty.py`` file shows up in ``file_stats`` with a single commit and
zero churn-classification - which only deepens the silent-SAFE problem:
a flagship command tells a consuming agent the project is **healthy 100/100**
when there is literally no code indexed.

Bug class: **Pattern 2 (silent fallback / silent success on degraded data)**,
flagship surface. Same shape as W834 (cmd_health on empty corpus) and W836
(cmd_doctor) - any command emitting a derived health/quality verdict from
queries that ALL returned empty rows must disclose ``state="empty_corpus"``
/ ``"no_symbols"`` / ``"not_initialized"`` + ``partial_success=True`` +
a verdict that explicitly names the empty condition.

Source-level branch points (``src/roam/commands/cmd_understand.py``):

* L739-741  - file/symbol/edge counts read (no zero-check applied)
* L774-797  - ``_find_entry_points`` / ``_key_abstractions`` / cluster query
              all return empty lists silently
* L843-854  - the verdict template:
                  ``f"{_health_label} {len(languages)}-lang project ...,
                   {len(clusters_data)} clusters, {len(hotspots)} hotspots"``
              ``_health_label`` is derived from ``health['health_score']``
              which is 100 for an empty corpus (no findings to deduct from)
* L883-940  - the JSON envelope emit; ``partial_success`` is auto-injected
              by ``json_envelope`` defaulting to False; no inspection of
              ``sym_count == 0`` before constructing the summary.

Fix template (separate wave, NOT this wave):

    if sym_count == 0:
        envelope.summary.update(
            verdict="no symbols indexed: run roam index first",
            partial_success=True,
            state="no_symbols",
            health_score=None,  # 100/100 on empty is misleading
        )

DO NOT FIX this wave - accumulate xfail-strict pin only.

Run isolation:
    python -m pytest tests/test_w805_j_cmd_understand_empty_corpus.py -x -n 0

Regression baseline (no dedicated ``test_understand*.py`` exists today):
    python -m pytest tests/test_w543_followup_migration.py \
                     tests/test_conventions_consolidation.py \
                     tests/test_caller_metric_definition.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# Drift-resistant repo-root resolution (W572 helper).
from tests._helpers.repo_root import repo_root  # noqa: F401,E402

# ---------------------------------------------------------------------------
# Helpers - invoke understand via the Click group so the top-level
# --json flag is honoured by ctx.obj.
# ---------------------------------------------------------------------------


def _invoke_understand(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam understand`` through the group so ``--json`` is honoured."""
    from roam.cli import cli

    args = []
    if json_mode:
        args.append("--json")
    args.append("understand")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file.

    The indexer runs cleanly but produces zero function/class/method
    symbols, zero edges, zero clusters and zero layers. ``understand``
    is forced down its no-data path; every architectural axis returns
    empty rows.
    """
    proj = tmp_path / "empty_understand_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    # Empty .py file: indexer sees one file, extracts zero symbols.
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols - regression baseline."""
    proj = tmp_path / "clean_understand_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        "class Config:\n    pass\n\ndef load_config():\n    return Config()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (properties already satisfied today)
# ---------------------------------------------------------------------------


class TestUnderstandEmptyCorpusSealed:
    """Properties already satisfied by the current understand envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam understand --json`` on an empty corpus exits 0."""
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        # Non-empty stdout (Pattern 1 variant C).
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries a non-empty verdict string."""
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        assert payload.get("command") == "understand"
        summary = payload.get("summary") or {}
        verdict = summary.get("verdict")
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string; got {verdict!r}"

    def test_empty_corpus_partial_success_key_present(self, cli_runner, empty_corpus):
        """``summary.partial_success`` key is auto-injected and present.

        Even on the empty branch, the envelope must DISCLOSE its
        partial-success state so consumers never have to guess from
        absence. The value may legitimately be ``False`` today (the
        Pattern-2 bug we're pinning); only the KEY presence is the
        sealed contract here.
        """
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success must be present (auto-injected); got summary keys = {sorted(summary.keys())}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: the verdict must work without any other field.

        Today the verdict reads
        ``"healthy 1-lang project (100/100), 0 clusters, 1 hotspots"``.
        That string IS self-describing prose (uses concrete numerics and
        anchored nouns like ``clusters`` / ``hotspots``); LAW 6 standalone
        readability is satisfied. The Pattern-2 BUG is the misleading
        ``"healthy"`` claim, not LAW-6 self-description - pinned separately
        in the xfail tests below.
        """
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        verdict = payload.get("summary", {}).get("verdict", "")
        # LAW 6: verdict must include enough structural cues to be parsed
        # without the surrounding envelope. Either ``project`` (the
        # canonical understand-verdict noun) or a percentage/score suffices.
        assert "project" in verdict.lower() or "/100" in verdict, (
            f"LAW 6: verdict must be self-describing standalone; got {verdict!r}"
        )

    def test_empty_corpus_agent_contract_facts_non_empty(self, cli_runner, empty_corpus):
        """``agent_contract.facts`` is non-empty even on the empty branch."""
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        contract = payload.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, (
            f"agent_contract.facts must be non-empty on empty corpus; got {facts!r}"
        )

    def test_empty_corpus_architecture_axes_all_empty(self, cli_runner, empty_corpus):
        """Architecture axes report zero on the empty corpus.

        This is the SHAPE of the bug, not the bug itself: the architecture
        sub-envelope correctly returns empty lists / zero counts. The
        Pattern-2 problem is that the top-level summary verdict does not
        REFLECT those empty axes - it claims ``healthy``. This test pins
        the architecture axes themselves so we can demonstrate the bug
        clearly: every axis is empty AND the verdict still says healthy.
        """
        result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        arch = payload.get("architecture") or {}
        assert arch.get("clusters") == [], f"empty corpus must report 0 clusters; got {arch.get('clusters')!r}"
        assert arch.get("key_abstractions") == [], (
            f"empty corpus must report 0 key_abstractions; got {arch.get('key_abstractions')!r}"
        )
        assert arch.get("entry_points") == [], (
            f"empty corpus must report 0 entry_points; got {arch.get('entry_points')!r}"
        )
        assert arch.get("layer_count") == 0
        project = payload.get("project") or {}
        assert project.get("symbols") == 0, f"empty corpus must report 0 symbols; got {project.get('symbols')!r}"

    def test_clean_corpus_emits_real_understanding(self, cli_runner, clean_corpus):
        """Regression baseline: a real-symbol corpus emits a non-trivial
        understand envelope with non-empty key_abstractions or
        entry_points and a verdict that reflects the real shape.
        """
        result = _invoke_understand(cli_runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0, f"clean corpus understand failed: {result.output}"
        payload = _json.loads(result.output)
        assert payload.get("command") == "understand"
        project = payload.get("project") or {}
        assert project.get("symbols", 0) > 0, f"clean corpus must report >0 symbols; got {project.get('symbols')!r}"
        verdict = payload.get("summary", {}).get("verdict", "")
        # Clean corpus verdict should match the canonical understand shape.
        assert "project" in verdict.lower() and "/100" in verdict, (
            f"clean corpus verdict should match understand shape; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# W978-confirmed REAL BUG - pinned via xfail-strict; will go green when fix lands.
#
# Bug: cmd_understand.py L843-854 (the JSON-mode verdict template) does NOT
# inspect symbol/cluster/layer/key_abstraction counts before assembling the
# verdict. On a 0-symbol corpus the verdict reads
#   "healthy 1-lang project (100/100), 0 clusters, 1 hotspots"
# identically to a healthy corpus. ``health_score`` is 100 because there are
# no findings to deduct from (the canonical "everything empty -> score 100"
# floor of ``collect_metrics``). ``summary.partial_success`` is auto-injected
# as False. No ``state`` field is emitted. This is Pattern 2 silent fallback,
# flagship class - the headline "single-call codebase comprehension" command
# tells a consuming agent the project is HEALTHY when there is no code at all.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-J BUG: cmd_understand.py L843-854 does not disclose "
        "empty-corpus state. Verdict says 'healthy 1-lang project "
        "(100/100), 0 clusters, 1 hotspots' on a 0-symbol corpus "
        "indistinguishably from a healthy project. Pattern 2 silent "
        "fallback (flagship class); awaiting separate fix wave."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, empty_corpus):
    """``summary.partial_success`` should be True on the empty branch.

    Pattern 2 (CLAUDE.md): an empty-data outcome must disclose
    ``partial_success=True``. Today the auto-inject defaults to False on
    every understand envelope, including the no-symbol case, because the
    command never sets it explicitly.
    """
    result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert summary.get("partial_success") is True, (
        f"partial_success should be True on empty corpus; got {summary.get('partial_success')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-J BUG: cmd_understand.py L883-940 does not emit summary.state. "
        "Empty corpus and healthy corpus are indistinguishable in the "
        "envelope state field. Pattern 2 silent fallback (flagship class); "
        "awaiting fix wave."
    ),
)
def test_empty_corpus_explicit_state(cli_runner, empty_corpus):
    """``summary.state`` should disclose the empty condition explicitly.

    Acceptable values (closed enum, mirroring W805-A/B/F pattern):
    ``empty_corpus``, ``no_symbols``, ``not_initialized``. Today the key
    is absent entirely - the consumer has to compare ``project.symbols``
    against a magic threshold to detect the empty case.
    """
    result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    state = summary.get("state")
    assert state in ("empty_corpus", "no_symbols", "not_initialized"), (
        f"summary.state must disclose empty condition; got {state!r}; summary keys = {sorted(summary.keys())}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-J BUG: cmd_understand.py L843-854 emits a 'healthy ... project' "
        "verdict identically on healthy and empty corpora. Worse, the "
        "health_score reads 100/100 on a 0-symbol corpus because there are "
        "no findings to deduct from. The verdict must mention 'empty' / "
        "'no symbols' / 'no data' / 'not initialized' on the empty branch. "
        "Pattern 2 silent fallback (flagship class); awaiting fix wave."
    ),
)
def test_no_silent_understood_on_empty(cli_runner, empty_corpus):
    """Verdict on the empty branch should NOT match the 'healthy N-lang
    project' shape - it must call out the empty condition explicitly so a
    consuming agent doesn't act on a 100/100 health score for a corpus
    with zero indexed symbols.
    """
    result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    verdict = payload.get("summary", {}).get("verdict", "").lower()
    # The bug: today verdict is literally
    #   "healthy 1-lang project (100/100), 0 clusters, 1 hotspots"
    # which is the success shape. Empty-state vocabulary must appear.
    empty_tokens = (
        "empty",
        "no symbol",
        "no data",
        "no files",
        "not initialized",
        "no index",
        "no code",
    )
    assert any(t in verdict for t in empty_tokens), f"verdict must disclose empty-corpus state; got {verdict!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-J BUG: cmd_understand.py L843-854 emits a 'healthy ... project' "
        "verdict on a 0-symbol corpus. Pattern 2 silent-SAFE: the flagship "
        "comprehension command claims architectural health when there is "
        "no architecture to comprehend. Pinned separately from the verdict "
        "test so the fix wave can address the architectural-overview "
        "guarantee independently."
    ),
)
def test_no_silent_architecture_overview_on_zero_symbols(cli_runner, empty_corpus):
    """The understand command must NOT emit a positive architectural
    verdict when every architectural axis (clusters, layers,
    key_abstractions, entry_points) is empty.

    A consuming agent reading the envelope should be able to short-circuit
    on either ``state == "no_symbols"`` OR a verdict that names the
    empty-architecture condition - NOT have to inspect five different
    arrays for emptiness.
    """
    result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    verdict = summary.get("verdict", "").lower()
    state = summary.get("state")
    arch = payload.get("architecture") or {}
    # Sanity: architecture really is empty on this fixture.
    assert arch.get("layers") == []
    assert arch.get("clusters") == []
    assert arch.get("key_abstractions") == []
    # The contract: at least ONE of state or verdict-tokens must disclose
    # the empty-architecture condition.
    empty_disclosed = (
        state in ("empty_corpus", "no_symbols", "not_initialized")
        or "no symbol" in verdict
        or "no architecture" in verdict
        or "empty" in verdict
        or "no code" in verdict
    )
    assert empty_disclosed, (
        f"empty-architecture must be disclosed via state OR verdict; state={state!r}, verdict={verdict!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-J BUG: cmd_understand.py never emits an explicit no_clusters / "
        "no_symbols / empty_corpus state on the architecture sub-envelope. "
        "Same root cause as test_empty_corpus_explicit_state; kept separate "
        "so the fix wave can verify the zero-clusters branch independently. "
        "Pattern 2 silent fallback."
    ),
)
def test_zero_clusters_explicit_state(cli_runner, empty_corpus):
    """A corpus with zero indexed clusters should emit either
    ``state == "empty_corpus" / "no_symbols"`` explicitly, OR a verdict
    that names the no-clusters condition (LAW 6 standalone).

    Today the verdict says ``0 clusters`` numerically but pairs it with
    ``healthy`` - the consumer cannot distinguish "0 clusters because
    nothing indexed" from "0 clusters because Louvain produced zero
    communities".
    """
    result = _invoke_understand(cli_runner, empty_corpus, json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    state = summary.get("state", "")
    verdict = summary.get("verdict", "").lower()
    zero_clusters_signalled = (
        state in ("empty_corpus", "no_symbols", "not_initialized")
        or "no cluster" in verdict
        or "no symbol" in verdict
        or "empty" in verdict
    )
    assert zero_clusters_signalled, (
        f"zero-clusters condition must be disclosed via state or verdict; state={state!r}, verdict={verdict!r}"
    )
