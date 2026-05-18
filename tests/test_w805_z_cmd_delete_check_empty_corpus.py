"""W805-Z -- empty-corpus Pattern-2 smoke test on ``roam delete-check``.

Twenty-sixth-in-batch W805 sweep. Diff-gating sibling of the W805-W
``refs-text`` empty-corpus probe. Per CLAUDE.md, ``roam delete-check``
"gates the diff on surviving references; exits 5 on BREAK-RISK with
--ci". Structural agent-safety peer of cmd_refs_text -- both answer
"is this thing still load-bearing?" but delete-check has the stronger
contract because of the CI exit-5 gate.

CRITICAL agent-safety class
---------------------------

The two dangerous verdicts in cmd_delete_check are:

  * ``SAFE`` (per-target) / ``overall: SAFE``
  * ``no deletions detected`` (summary; clean-tree path)

Both signal "you can proceed". An agent or CI job that switches on
either of these and pushes the change forward could ship a deletion
that is genuinely load-bearing -- the engine simply didn't see the
surviving reference because the corpus is unindexed / nearly empty.

Pattern-2 silent SAFE on the zero-survivors path is the worst possible
failure mode for the gate: the verdict that admits the deletion (and,
under ``--ci``, exit 0 NOT exit 5) is emitted purely because
``run_search`` returned zero matches. The envelope today does not
distinguish "genuinely no survivors" from "couldn't scan anywhere".

Scope
-----

cmd_delete_check has three zero-deletion / zero-survivors emission paths:

1. Empty diff (lines 290-315 ``if not diff.strip():``). Returns
   ``verdict: "no deletions detected"``, no ``state`` /
   ``partial_success``. The text path emits ``VERDICT: no deletions
   detected -- nothing to check.``

2. Diff without symbol/file deletions (lines 336-358 ``if not targets:``).
   Returns ``verdict: "no symbol or file deletions detected"``, same
   shape -- no ``state`` / ``partial_success``.

3. Per-target zero-survivors (``_verdict`` lines 566-579): when
   ``run_search`` returns zero matches for a target, ``_verdict``
   returns ``("SAFE", "no surviving references")``. The overall
   envelope verdict is then ``"N deletion(s): 0 break-risk, ..., M
   safe"`` with ``overall: "SAFE"`` and ``partial_success: false``.
   **Pattern-2 silent SAFE candidate; structural peer of W805-W.**

W978 first-hypothesis check
---------------------------

First hypothesis: the per-target zero-survivors path emits SAFE on
indistinguishable inputs -- "deleted a genuinely unused symbol" looks
identical to "deleted something where the engine couldn't scan the
callers". The probe (this commit, isolation run) confirms:

* Tiny corpus (just README + one Python file with no callers) +
  delete the file:
  - Per-deletion verdict: ``SAFE``, reason ``"no surviving references"``
  - Summary verdict: ``"2 deletion(s): 0 break-risk, 0 likely-safe, 2 safe"``
  - ``overall: "SAFE"``
  - ``partial_success: false``
  - No ``state`` field on summary

  An agent / CI gate seeing this output would conclude the deletion is
  safe to ship, even though the only reason zero survivors were found
  is that there's no code in the corpus that COULD survive.

* Empty diff (no working changes): verdict ``"no deletions detected"``,
  no ``state`` / ``partial_success``. Less critical (nothing to delete
  -> nothing to gate), but still emits the implicit "proceed" signal
  with no disclosure of the empty-input condition.

* Genuine BREAK-RISK regression (foo deleted, separate bar.py still
  calls it): correctly emits ``verdict: "BREAK-RISK"``, ``overall:
  "BREAK-RISK"``, with surviving reference enumerated. The bug is
  ONLY on the zero-survivors degenerate path.

Conclusion
----------

* **REAL BUG pinned -- W805-Z severity peer of W805-W**:
  src/roam/commands/cmd_delete_check.py:566-579 (``_verdict``). The
  zero-survivors branch emits ``SAFE`` unconditionally with no
  ``state`` / ``partial_success`` disclosure. An agent feeding
  delete-check a deletion in a sparsely-indexed corpus (CI on a fresh
  clone before ``roam init``, partial reindex, deletion of files in
  bridges roam can't traverse) cannot distinguish "no callers exist"
  from "couldn't scan callers". The CI gate (exit 5 only fires on
  BREAK-RISK) silently passes the change. CRITICAL agent-safety class
  -- the gate that's supposed to BLOCK destructive deletions
  rubber-stamps them on the empty-corpus path. Pinned strict so a
  future cleanup that distinguishes those two states graduates the
  test to PASS without manual edit.

* **Shape parity (mild)**: empty-diff and no-targets paths (lines
  290-358) also lack ``state`` / ``partial_success`` disclosure.
  Less critical than the zero-survivors-with-deletions case because
  ``"no deletions detected"`` is at least a more specific human
  reading than ``SAFE`` -- but the closed-enum contract today does
  not distinguish "no diff" from "diff scanned, found nothing".
  Pinned strict for symmetry.

Sweep brief: W805-Z (Wave805-Z, twenty-sixth-in-batch). Structural
peer of W805-W (cmd_refs_text). Same agent-safety class -- diff-gating
"can I delete this?" command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no indexable source symbols, no diff.

    Exercises the empty-diff branch (lines 290-315): ``_git_diff`` returns
    an empty diff because the working tree matches HEAD.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def empty_corpus_with_deletion(tmp_path):
    """Tiny corpus + a deletion that produces zero survivors.

    A lonely .py file with one symbol and no callers anywhere. Committed
    + indexed, then deleted in the working tree. ``run_search`` returns
    zero matches across the working tree because the file is gone and
    nothing else references it -- this is the Pattern-2 silent SAFE
    candidate: the verdict is SAFE on an effectively empty corpus.
    """
    proj = tmp_path / "empty_corpus_deletion"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Tiny corpus.\n")
    (proj / "lonely.py").write_text("def lonely_fn():\n    return 1\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Delete the file in the working tree (no separate commit -- working diff).
    (proj / "lonely.py").unlink()
    return proj


@pytest.fixture
def break_risk_corpus(tmp_path):
    """Genuine BREAK-RISK: foo defined in foo.py, called from bar.py.

    Delete foo's definition, leave bar.py untouched. Survivors should
    fire BREAK-RISK -- the gate's positive-path regression check.
    """
    proj = tmp_path / "break_risk_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "foo.py").write_text("def foo():\n    return 1\n")
    (src / "bar.py").write_text("from src.foo import foo\n\ndef bar():\n    return foo()\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Remove foo's def in the working tree.
    (src / "foo.py").write_text("# foo removed\n")
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on degenerate paths.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The empty-diff and zero-survivors branches must always emit a
    structured envelope, never crash and never emit empty stdout
    (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """Empty diff path: no exception, non-empty stdout."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"delete-check must exit 0 on empty diff per current contract; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on empty-diff path"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty summary verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # Existing shape on the empty-diff branch: "no deletions detected".
        assert "deletion" in verdict.lower(), f"summary verdict must mention deletions; got {verdict!r}"

    def test_zero_survivors_no_crash(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """Zero-survivors path: no exception, non-empty stdout."""
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"delete-check must exit 0 on zero-survivors per current contract; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on zero-survivors path"


# ---------------------------------------------------------------------------
# LAW 6 -- summary verdict works standalone.
# ---------------------------------------------------------------------------


class TestLaw6VerdictStandalone:
    """The summary verdict on each degenerate path must be informative
    without any other field (LAW 6)."""

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-diff verdict text is concrete-noun-anchored + standalone."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        verdict = data["summary"].get("verdict", "")
        assert verdict.strip(), "verdict empty"
        # Anchored on "detected" (a LAW 4 past-participle anchor).
        assert "deletion" in verdict.lower(), f"verdict must mention deletions; got {verdict!r}"

    def test_zero_survivors_law6_verdict_standalone(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """Zero-survivors verdict text is informative standalone."""
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        verdict = data["summary"].get("verdict", "")
        assert verdict.strip(), "verdict empty"
        # Shape: "N deletion(s): X break-risk, Y likely-safe, Z safe".
        assert "deletion" in verdict.lower(), f"verdict must mention deletions; got {verdict!r}"
        # LAW 4: anchored on the concrete-noun terminal "safe" via the
        # count-bearing summary.
        assert any(term in verdict.lower() for term in ("safe", "break-risk", "likely-safe")), (
            f"verdict must name a closed-enum bucket; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 silent SAFE on the empty-diff branch.
# Pinned strict -- shape parity with W805-W.
# ---------------------------------------------------------------------------


class TestEmptyDiffSilentSafe:
    """Empty-diff path emits a 'proceed' verdict (``no deletions detected``)
    with no explicit state disclosure. Less critical than zero-survivors
    because the human reading is more specific, but the machine-readable
    state contract is the same: no ``state`` / no ``partial_success``."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-Z shape parity with W805-W: "
            "src/roam/commands/cmd_delete_check.py:290-315 (empty-diff "
            "branch) emits ``verdict: 'no deletions detected'`` with no "
            "``summary.state`` disclosure. The canonical Pattern-2 contract "
            "is to name the empty-input condition explicitly so machine "
            "consumers can switch on state, not text-match the verdict. "
            "Pinned strict so a future cleanup that adds "
            "``state: 'no_diff'`` graduates to PASS."
        ),
    )
    def test_empty_diff_explicit_state(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-diff path discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        state = summary.get("state")
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-Z Pattern-2: empty-diff path must emit summary.state "
            f"to distinguish 'no diff' from 'diff scanned, found nothing'; "
            f"got {state!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 silent SAFE on the zero-survivors path.
# REAL BUG pinned strict -- CRITICAL agent-safety class (peer of W805-W).
# ---------------------------------------------------------------------------


class TestZeroSurvivorsSilentSafe:
    """The most agent-safety-critical Pattern-2 case in cmd_delete_check:
    the CI gate that's supposed to BLOCK destructive deletions emits
    ``overall: SAFE`` with no state disclosure on the zero-survivors
    path. An agent acting on this verdict could ship a deletion of a
    load-bearing symbol the corpus simply couldn't scan."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-Z REAL BUG: src/roam/commands/cmd_delete_check.py:566-579 "
            "(``_verdict``) emits SAFE on the zero-survivors path with no "
            "``summary.state`` disclosure. An agent or CI gate switching "
            "on machine-readable state cannot tell 'genuinely no callers' "
            "from 'corpus couldn't be scanned'. CRITICAL agent-safety "
            "class -- the gate that should BLOCK destructive deletions "
            "rubber-stamps them on the empty-corpus path (and exit 5 "
            "never fires under --ci because overall != BREAK-RISK). "
            "Structural peer of W805-W on cmd_refs_text. Pinned strict so "
            "a future cleanup that adds ``state: 'empty_corpus'`` (or "
            "equivalent) graduates this to PASS."
        ),
    )
    def test_zero_survivors_explicit_state(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """Zero-survivors path discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        state = summary.get("state")
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-Z Pattern-2 silent SAFE: zero-survivors path must emit "
            f"summary.state to distinguish 'truly no callers' from 'couldn't "
            f"scan callers'; got {state!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-Z REAL BUG: src/roam/commands/cmd_delete_check.py:566-579 "
            "emits ``partial_success: false`` on the zero-survivors path. "
            "When the underlying outcome is 'we couldn't find any "
            "survivors' AND the verdict is SAFE, an agent reading "
            "partial_success would conclude no degradation occurred. The "
            "canonical Pattern-2 contract sets partial_success=True on any "
            "'empty input / degraded scan' outcome. Pinned strict; "
            "CRITICAL agent-safety class."
        ),
    )
    def test_zero_survivors_partial_success_set(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """Pattern-2 guard: zero-survivors path sets partial_success=True."""
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-Z Pattern-2: zero-survivors path must set "
            f"partial_success=True; got {summary.get('partial_success')!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-Z REAL BUG -- CRITICAL agent-safety class: "
            "src/roam/commands/cmd_delete_check.py:566-579 unconditionally "
            "stamps per-target ``verdict: 'SAFE'`` on every zero-survivors "
            "target. An agent or CI gate acting on this verdict against an "
            "empty / unindexed corpus could ship a deletion of a symbol "
            "that IS load-bearing in source files the engine couldn't "
            "read. The canonical contract on a zero-survivors path with an "
            "effectively empty corpus should be ``UNKNOWN`` / "
            "``INSUFFICIENT-DATA`` (or the existing SAFE PLUS a "
            "state='empty_corpus' disclosure agents can switch on). Pinned "
            "strict so the fix graduates to PASS. Peer of W805-W on "
            "cmd_refs_text -- same severity class."
        ),
    )
    def test_no_silent_safe_on_empty(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """CRITICAL: SAFE on an unscannable corpus is agent-unsafe.

        Either the per-target verdict changes to a non-SAFE marker on
        the empty-corpus path, OR the envelope discloses an explicit
        ``state`` field that an agent can switch on before acting.
        Today neither is true.
        """
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        per_results = data.get("deletions", [])
        assert per_results, f"expected at least one deletion record; got {per_results}"
        any_safe = any("SAFE" in (d.get("verdict") or "").upper() for d in per_results)
        summary_state = data["summary"].get("state")
        state_discloses_empty = summary_state is not None and "empty" in str(summary_state).lower()
        # Fix graduates either by non-SAFE verdict OR by explicit state.
        assert (not any_safe) or state_discloses_empty, (
            f"W805-Z CRITICAL agent-safety: SAFE on zero-survivors path MUST "
            f"be accompanied by a state disclosure that an agent can use to "
            f"detect 'corpus couldn't be scanned'. "
            f"Got per-target verdicts={[d.get('verdict') for d in per_results]!r}, "
            f"state={summary_state!r}."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-Z REAL BUG -- CRITICAL CI gate semantics: "
            "src/roam/commands/cmd_delete_check.py:562-563 only raises exit 5 "
            "when ``any_break`` is True. On the zero-survivors path the "
            "verdict is SAFE so ``any_break`` is False and the gate exits 0 "
            "under ``--ci``, SILENTLY PASSING the deletion. The gate's "
            "exit-5 BREAK-RISK semantic should ALSO fire on the "
            "insufficient-data path (empty corpus + ``--ci``) -- a CI "
            "runner that can't gate the deletion must NOT pass it. "
            "Pinned strict so a future cleanup that escalates the "
            "empty-corpus case to exit 5 under ``--ci`` graduates to PASS."
        ),
    )
    def test_no_silent_no_break_risk_on_empty_ci(self, cli_runner, empty_corpus_with_deletion, monkeypatch):
        """CI gate exit-5 should NOT degrade to 0 silently on empty corpus."""
        monkeypatch.chdir(empty_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--ci"],
            cwd=empty_corpus_with_deletion,
            json_mode=True,
        )
        # The CI gate either fails loudly (exit 5) OR passes only if the
        # envelope discloses the empty-corpus condition. Today neither is
        # true: exit 0 with overall=SAFE and no state.
        EXIT_GATE_FAILURE = 5
        if result.exit_code == EXIT_GATE_FAILURE:
            return
        # If exit 0, the envelope must disclose the degraded state.
        data = parse_json_output(result, "delete-check")
        summary_state = data["summary"].get("state")
        assert summary_state is not None and "empty" in str(summary_state).lower(), (
            f"W805-Z CRITICAL: CI gate exit 0 on zero-survivors empty corpus "
            f"MUST disclose ``summary.state`` (or exit 5). Got exit "
            f"{result.exit_code}, state={summary_state!r}."
        )


# ---------------------------------------------------------------------------
# Positive regression -- a genuine BREAK-RISK still produces a real verdict.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullAudit:
    """Sanity: a real surviving reference produces a real BREAK-RISK
    audit envelope. Guards against a fix-forward that over-corrects the
    empty-corpus case and silences genuine BREAK-RISK signals."""

    def test_clean_corpus_emits_real_audit(self, cli_runner, break_risk_corpus, monkeypatch):
        """foo deleted; bar.py still calls foo() -> BREAK-RISK."""
        monkeypatch.chdir(break_risk_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=break_risk_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        # Overall verdict is BREAK-RISK.
        summary = data["summary"]
        assert summary.get("overall") == "BREAK-RISK", (
            f"genuine BREAK-RISK corpus must emit overall=BREAK-RISK; got {summary.get('overall')!r}"
        )
        assert summary.get("break_risk", 0) >= 1, (
            f"genuine BREAK-RISK corpus must have >=1 break_risk count; got {summary.get('break_risk')!r}"
        )
        # At least one per-target deletion is flagged BREAK-RISK with survivors.
        deletions = data.get("deletions", [])
        assert any(d.get("verdict") == "BREAK-RISK" and d.get("survivors") for d in deletions), (
            f"expected at least one BREAK-RISK deletion with survivors; got {deletions}"
        )

    def test_clean_corpus_ci_exit_5(self, cli_runner, break_risk_corpus, monkeypatch):
        """Genuine BREAK-RISK + --ci must exit 5 (gate-failure semantic)."""
        monkeypatch.chdir(break_risk_corpus)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--ci"],
            cwd=break_risk_corpus,
        )
        EXIT_GATE_FAILURE = 5
        assert result.exit_code == EXIT_GATE_FAILURE, (
            f"genuine BREAK-RISK with --ci must exit {EXIT_GATE_FAILURE}; got {result.exit_code}\n{result.output}"
        )
