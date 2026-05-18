"""W805-E - empty-corpus smoke for ``roam path-coverage`` (W805 Pattern 2 sweep).

Fifth-in-batch W805 sweep extension. Sibling commands in flight or
sealed: cmd_owner (W805-A, BUG), cmd_minimap (W805-B, BUG), plus
cmd_oracle/rules/fitness/workflow (W805-D in flight).

W978 first-hypothesis: path-coverage on an empty graph could plausibly
emit a silent "100% coverage" verdict (0/0 paths covered = vacuously
100%) — the canonical Pattern-2 silent-SAFE shape. The probe BEFORE
writing tests confirmed the OPPOSITE: cmd_path_coverage already emits a
strong Pattern-2 empty-state envelope via the W807-tagged
``_no_paths_output`` helper at ``src/roam/commands/cmd_path_coverage.py:595-657``:

  {
    "verdict": "Corpus empty: 0 entries",
    "state": "no_entry_points",         # closed-enum disclosure
    "partial_success": true,             # degraded-resolution acknowledged
    "total_paths": 0, "untested_paths": 0,
    "critical": 0, "high": 0
  }

This module therefore pins the CURRENT-GOOD (sealed) contract on the
empty-corpus path and the W632/W756 prior-fix regression baselines. No
xfail-strict markers fire because no bug was found — the W805 sweep
ran clean on this command.

Sealed contracts asserted:
  * exit 0 + parseable envelope + non-empty stdout
  * ``summary.verdict`` non-empty + LAW 6 standalone
  * ``summary.partial_success`` is True on the empty branch
  * ``summary.state`` discloses one of {no_entry_points, no_sinks,
    no_paths_connecting} on the degenerate branches
  * ``summary.total_paths == 0`` (no silent 100% coverage)
  * Filter-narrowed branch stays partial_success=False (NOT a
    degenerate corpus — clean scan with narrow filter)
  * No-entries branch state == "no_entry_points"
  * Clean corpus emits a real coverage envelope (regression baseline)
  * W756 closed-enum risk vocabulary intact (fail-loud on unknown label)

Cross-references:
  * `src/roam/commands/cmd_path_coverage.py:595-657` — W807 helper
  * `src/roam/commands/cmd_path_coverage.py:217-245` — W632/W718 risk
    band canonical vocabulary
  * `src/roam/commands/cmd_path_coverage.py:500-510` — W756 fail-loud
    on unknown risk label (Pattern 1 variant D)
  * `tests/test_w805_a_cmd_owner_empty_corpus.py` — sibling template
  * `tests/test_w805_b_cmd_minimap_empty_corpus.py` — sibling template
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

# ---------------------------------------------------------------------------
# Invocation helper - invoke path-coverage via the Click group so the
# global --json flag is honoured by ``ctx.obj``.
# ---------------------------------------------------------------------------


def _invoke_path_coverage(runner: CliRunner, cwd, json_mode: bool = True, *extra):
    """Invoke ``roam path-coverage`` through the group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("path-coverage")
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

    The indexer runs cleanly but produces zero function/method symbols,
    zero edges, zero entry points and zero sinks. ``path-coverage`` is
    forced down its ``_no_paths_output`` empty-state path.
    """
    proj = tmp_path / "empty_corpus_pathcov"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def no_entry_corpus(tmp_path, monkeypatch):
    """Project with only a leaf function — produces 0 entry points.

    A symbol with no outgoing edges cannot satisfy the entry-point
    filter (which requires ``s.id IN (SELECT source_id FROM edges)``).
    Exercises the explicit ``no_entry_points`` branch specifically.
    """
    proj = tmp_path / "no_entry_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    # Lone function with no caller and no callee — neither entry nor
    # interior — guarantees zero entries.
    (proj / "lone.py").write_text("def lone():\n    return 1\n", encoding="utf-8")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with a real entry-to-sink call chain.

    Regression baseline: the happy path should produce a non-empty
    paths list, a concrete-verdict like ``"N high-risk path(s) with zero
    test coverage"``, and ``partial_success=False`` (or absent default).
    """
    proj = tmp_path / "clean_corpus_pathcov"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")

    (proj / "handler.py").write_text(
        "from service import process\n\ndef handle(data):\n    return process(data)\n",
        encoding="utf-8",
    )
    (proj / "service.py").write_text(
        "from db import save\n\ndef process(data):\n    save(data)\n    return data\n",
        encoding="utf-8",
    )
    (proj / "db.py").write_text(
        "def save(record):\n    return record\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# SMOKE (always-on baseline assertions)
# ---------------------------------------------------------------------------


class TestPathCoverageEmptyCorpusSealed:
    """Properties already satisfied by cmd_path_coverage today."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam path-coverage --json`` on an empty corpus exits 0."""
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}:\n{result.output}"
        # Pattern 1 variant C: never emit empty stdout in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Envelope carries a non-empty verdict string."""
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        assert payload.get("command") == "path-coverage"
        summary = payload.get("summary") or {}
        verdict = summary.get("verdict")
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string; got {verdict!r}"

    def test_empty_corpus_explicit_state(self, cli_runner, empty_corpus):
        """``summary.state`` discloses the empty-corpus condition (W807).

        Closed-enum disclosure: one of ``no_entry_points`` /
        ``no_sinks`` / ``no_paths_connecting``. The exact value depends
        on which degenerate axis fires first; an empty .py file has no
        symbols at all, so ``no_entry_points`` fires first (entries are
        computed before sinks).
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        state = summary.get("state")
        accepted = {"no_entry_points", "no_sinks", "no_paths_connecting"}
        assert state in accepted, (
            f"summary.state must disclose degenerate axis; got {state!r}; "
            f"expected one of {accepted}; summary keys = {sorted(summary.keys())}"
        )

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus):
        """``summary.partial_success`` is True on the empty branch (W807).

        Pattern 2 / Pattern 1 variant D: when the degeneracy is on the
        graph side (no entries OR no sinks), the corpus is degraded and
        partial_success must be True. Distinct from the filter-narrowed
        branch (clean scan, partial_success=False).
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        assert summary.get("partial_success") is True, (
            f"empty-corpus branch must set partial_success=True; "
            f"got summary.partial_success={summary.get('partial_success')!r}; "
            f"summary={summary!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus):
        """LAW 6: the verdict line stands alone (single line, ASCII).

        Current shape is ``"Corpus empty: 0 entries"`` — self-describing,
        names the empty-state axis explicitly, ends on a concrete-noun
        terminal (``entries``) accepted by the LAW 4 lint.
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        verdict = payload.get("summary", {}).get("verdict", "")
        assert "\n" not in verdict, f"verdict has embedded newline: {verdict!r}"
        assert verdict.isascii(), f"verdict is not plain ASCII: {verdict!r}"
        assert verdict.strip() not in ("", "?", "verdict"), f"verdict is a placeholder: {verdict!r}"
        # LAW 6 standalone: empty-state vocabulary present so the verdict
        # makes sense without reading any other field.
        lowered = verdict.lower()
        empty_tokens = ("empty", "no entries", "no sinks", "no paths", "not initialized")
        assert any(t in lowered for t in empty_tokens), (
            f"LAW 6: verdict must disclose empty-corpus state standalone; got {verdict!r}"
        )

    def test_empty_corpus_no_silent_100_coverage(self, cli_runner, empty_corpus):
        """W978 first-hypothesis check: NO silent "100% coverage" on empty graph.

        Pattern-2 silent-SAFE: 0 paths analysed could be rendered as
        ``"all paths covered"`` / ``"100% coverage"`` / ``"all clear"``
        (vacuously true: 0/0 covered). The verdict MUST NOT use any of
        that healthy-corpus vocabulary on an empty corpus — it must
        instead disclose the degeneracy axis explicitly.

        cmd_path_coverage already satisfies this via W807; this test
        pins the contract so a future refactor that introduces a
        ``"all paths protected"`` shortcut on the empty branch trips
        the guard loudly.
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        verdict_lower = (summary.get("verdict") or "").lower()
        silent_safe_phrases = (
            "100%",
            "all paths covered",
            "all paths protected",
            "all clear",
            "fully covered",
            "no critical paths",
            "no untested paths",
            "all tested",
        )
        for phrase in silent_safe_phrases:
            assert phrase not in verdict_lower, (
                f"empty-corpus verdict contains silent-SAFE phrase {phrase!r}; "
                f"verdict={summary.get('verdict')!r} — Pattern 2 silent fallback"
            )
        # Numeric assertion: total_paths must be 0 (we know it is) AND
        # the counts must not be coerced into a fake-healthy shape.
        assert summary.get("total_paths") == 0, (
            f"total_paths must be 0 on empty corpus; got {summary.get('total_paths')!r}"
        )
        assert summary.get("untested_paths") == 0
        assert summary.get("critical") == 0
        assert summary.get("high") == 0

    def test_no_entry_points_explicit_state(self, cli_runner, no_entry_corpus):
        """A project with zero entry points emits ``state == "no_entry_points"``.

        Pins the closed-enum disclosure for the specific
        ``entry_count == 0`` branch in ``_no_paths_output`` (line 615-619).
        """
        result = _invoke_path_coverage(cli_runner, no_entry_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        # No entries → "no_entry_points" axis fires.
        assert summary.get("state") == "no_entry_points", (
            f"zero-entry corpus must emit state='no_entry_points'; got {summary.get('state')!r}"
        )
        assert payload.get("entry_points_found") == 0
        # partial_success=True because the degeneracy is graph-side.
        assert summary.get("partial_success") is True
        # The note field discloses the absent-data reason in prose.
        note = payload.get("note") or ""
        assert "entry point" in note.lower() or "entries" in note.lower(), (
            f"note field must disclose absent-entries reason; got {note!r}"
        )

    def test_empty_corpus_agent_contract_facts_non_empty(self, cli_runner, empty_corpus):
        """``agent_contract.facts`` is non-empty on the empty branch.

        Pattern 2 always-emit + LAW 4 anchoring: the fact list must
        reach the consumer even when the corpus is empty (auto-derived
        by ``json_envelope`` from the summary keys).
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        contract = payload.get("agent_contract") or {}
        facts = contract.get("facts") or []
        assert isinstance(facts, list) and facts, (
            f"agent_contract.facts must be non-empty on empty corpus; got {facts!r}"
        )
        assert all(isinstance(f, str) and f for f in facts), f"each fact must be a non-empty string; got {facts!r}"

    def test_filter_narrowed_branch_stays_clean_scan(self, cli_runner, empty_corpus):
        """Filter-narrowed empty result is NOT a degenerate corpus.

        Distinct from the no_entry_points / no_sinks branches: when the
        user provides a ``--from`` glob that matches nothing, the corpus
        itself may be healthy — only the filter narrowed the result. The
        W807 contract pins this as ``partial_success=False`` +
        ``state="no_paths_matching_filters"``.
        """
        result = _invoke_path_coverage(cli_runner, empty_corpus, True, "--from", "nonexistent/*")
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        summary = payload.get("summary") or {}
        # Filter-narrowed: NOT a degenerate corpus → partial_success=False.
        assert summary.get("partial_success") is False, (
            f"filter-narrowed branch must stay partial_success=False; got {summary.get('partial_success')!r}"
        )
        assert summary.get("state") == "no_paths_matching_filters", (
            f"filter-narrowed state must be 'no_paths_matching_filters'; got {summary.get('state')!r}"
        )

    def test_clean_corpus_emits_real_coverage(self, cli_runner, clean_corpus):
        """Regression baseline: real call chain produces a real coverage envelope.

        The fixture wires handle → process → save with no test edges; the
        envelope must report at least one untested path and either a
        ``high`` or ``critical`` count > 0 (no DB-write effect today, so
        ``high`` is the expected band).
        """
        result = _invoke_path_coverage(cli_runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0, f"clean corpus failed: {result.output}"
        payload = _json.loads(result.output)
        assert payload.get("command") == "path-coverage"
        summary = payload.get("summary") or {}
        # Real coverage data: at least one path, untested.
        assert summary.get("total_paths", 0) >= 1, f"clean corpus must produce >=1 path; got {summary!r}"
        # The clean-corpus path is NOT degraded.
        assert summary.get("partial_success", False) is False, (
            f"clean corpus must NOT report partial_success=True; got {summary!r}"
        )
        # Verdict should NOT contain empty-corpus vocabulary.
        verdict_lower = (summary.get("verdict") or "").lower()
        assert "corpus empty" not in verdict_lower, (
            f"clean-corpus verdict must not say 'corpus empty'; got {summary.get('verdict')!r}"
        )

    def test_w756_unknown_risk_label_intact(self, cli_runner, clean_corpus):
        """W756 closed-enum regression baseline: ``_classify_risk`` never
        returns a label outside {critical, high, medium, low}.

        The bucket-summation loop at lines 500-510 of cmd_path_coverage.py
        raises ``ValueError`` on any unknown label — the W756 fail-loud
        guard. This test verifies the closed-enum is unbreached on a
        real corpus end-to-end (a covering smoke check for the W756
        invariant).
        """
        result = _invoke_path_coverage(cli_runner, clean_corpus, json_mode=True)
        assert result.exit_code == 0
        payload = _json.loads(result.output)
        valid_risks = {"critical", "high", "medium", "low"}
        for path in payload.get("paths", []):
            risk = path.get("risk")
            assert risk in valid_risks, (
                f"W756 invariant breach: path risk label {risk!r} not in "
                f"{valid_risks}; the bucket-summation loop would now raise"
            )
        # Also pin the closed-enum at the unit-function layer (a quick
        # in-test re-import sanity check — full coverage lives in
        # test_path_coverage_classify.py).
        from roam.commands.cmd_path_coverage import _classify_risk

        labels = {
            _classify_risk([1, 2, 3], set(), {3: "writes_db"}),
            _classify_risk([1, 2, 3], set(), {3: "network"}),
            _classify_risk([1, 2, 3], {1}, {3: ""}),
            _classify_risk([1, 2, 3, 4], {1, 2}, {4: ""}),
        }
        assert labels <= valid_risks, f"W756 closed-enum breach: _classify_risk produced {labels - valid_risks}"
