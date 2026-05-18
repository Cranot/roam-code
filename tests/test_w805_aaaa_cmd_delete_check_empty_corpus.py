"""W805-AAAA -- diff-source-axis Pattern-1-V-D + Pattern-2 probe on
``roam delete-check``.

Eighteenth-in-batch W805 sweep (this micro-batch). COMPLETES the
index-aware text-search trio audit (grep W805-UUU -> refs-text W805-XXX
-> delete-check W805-AAAA). Per CLAUDE.md, ``roam delete-check``
"gates the diff on surviving references; exits 5 on BREAK-RISK with
``--ci``."

W978 first-hypothesis: distinct axis from W805-Z + W607-J
---------------------------------------------------------

Before writing this file, audited ``cmd_delete_check.py`` head-to-tail
and the two pre-existing pin files:

  * ``test_w805_z_cmd_delete_check_empty_corpus.py`` -- pins the
    Pattern-2 silent SAFE on the EMPTY-CORPUS / ZERO-SURVIVORS path
    (per-target ``SAFE`` verdict, no ``state``/``partial_success``
    disclosure on unscannable corpus).
  * ``test_w607_j_cmd_delete_check_warnings_out_envelope.py`` -- pins
    the SUBPROCESS-AXIS ``warnings_out`` disclosure (engine fan-out,
    ``_git_diff`` failure surfaced via ``delete_check_*`` markers).

W805-AAAA isolates a THIRD axis: the DIFF-SOURCE resolution disclosure
on the ``_git_diff`` non-zero-return path
(``cmd_delete_check.py:135-137`` + the consumer at lines 296-343). Two
distinct USER errors collapse into the same generic ``git_error`` shape:

  1. **Bogus commit range** (e.g. ``--commit-range
     nonexistent_branch..HEAD``). The git binary is present, runs
     cleanly, returns non-zero because the ref doesn't exist. The
     ``git_error`` sentinel today gives the agent NO way to distinguish
     this user-fixable typo from an operational ``git ran but errored``
     class of failure.

  2. **``--source pr`` with no PR context** (e.g. ``--base-ref
     nonexistent_branch``, or running in a freshly-cloned repo where
     ``origin/main`` doesn't exist locally). Same ``git_error``
     sentinel. Same loss of actionability.

Both are Pattern-1-V-D resolution-disclosure gaps on the diff-source
resolution axis (analogous to ``--reachable-from <bogus>`` on
cmd_refs_text W805-XXX / cmd_grep W805-UUU). The current envelope
emits ``partial_success: true`` + ``git_error: "git_error"`` but NEITHER
``summary.state`` NOR ``summary.resolution`` -- the closed-enum
disclosure an agent could switch on.

Probe results (this commit, /tmp/w805_aaaa_probe isolation run)
---------------------------------------------------------------

* ``--commit-range nonexistent_branch..HEAD``:
  exit 0, ``partial_success: true``, ``git_error: "git_error"``,
  ``warnings_out: ["delete_check_git_diff_failed:git_error:source='working' cannot read diff"]``,
  ``verdict: "diff unavailable: git_error -- cannot gate"``.
  NO ``summary.state``, NO ``summary.resolution``. The agent
  receives a generic "cannot gate" with no way to distinguish "the
  user typo'd the ref" from "the host has a broken git install".

* ``--source pr --base-ref nonexistent_branch``: identical shape.
  ``warnings_out`` mentions ``source='pr'`` but again no
  ``state``/``resolution`` closed-enum disclosure.

* ``--source pr --base-ref nonexistent_branch --ci``: exit 5
  (fail-loud behavior already correct on the CI-gate axis -- W607-J
  pre-sealed this part).

CRITICAL agent-safety class
---------------------------

The exit-5 fail-loud path is already correct on this branch (W607-J's
verifiable contract). The W805-AAAA pin is on the RESOLUTION
DISCLOSURE axis -- a Pattern-1-V-D contract gap that doesn't change
the gate decision but does change what an agent does AFTER the gate
fails. Without ``state``/``resolution``, the agent's only recovery
path is to re-read the human-readable verdict string, which violates
LAW 4 (machine consumers should switch on closed-enum machine state,
not text-match diagnostic strings).

W607-J orthogonality
--------------------

W607-J markers (``delete_check_*``) ARE expected to fire on the
diff-source error path (W607-J already pins this on the
``_git_diff`` subprocess axis -- ``warnings_out`` lineage IS present
post-W607-J). The W805-AAAA axis is COMPLEMENTARY: it pins the
MISSING ``state``/``resolution`` closed-enum disclosure on the SAME
event. W607-J = subprocess-degrade lineage marker. W805-AAAA =
resolution-state closed-enum disclosure. Both must coexist; W805-AAAA
graduates WHEN AND ONLY WHEN ``state``/``resolution`` are added to
the envelope.

W978 + W907 compliance
----------------------

* W978: probed in isolation (/tmp/w805_aaaa_probe). Confirmed the
  diff-source-resolution-disclosure axis is distinct from W805-Z's
  empty-corpus state-disclosure axis AND from W607-J's subprocess
  warnings_out axis. The bogus-commit-range path doesn't trigger
  the W805-Z fixtures (corpus is populated; deletion exists) and
  isn't blocked by the W607-J markers (those fire on a DIFFERENT
  contract).
* W907: no false-cycle docstrings. The ``_git_diff`` sentinel
  collapse to a single ``git_error`` string is intentional in the
  current code; the pin asks for a closed-enum split, not a
  speculative refactor.

W805 sweep
----------

Per task spec this is the eighteenth-in-batch W805 sweep micro-batch
(grep W805-UUU + refs-text W805-XXX + delete-check W805-AAAA). The
batch completes the index-aware text-search trio audit. Next
candidate (W805-BBBB): the diff-source-axis sibling check on
``cmd_pr_diff`` / ``cmd_diff`` (same ``_git_diff``-shaped subprocess
call, same Pattern-1-V-D-on-resolution disclosure question).
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
    """Project with only a README -- no indexable source symbols, no diff."""
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def populated_corpus_with_deletion(tmp_path):
    """Populated indexed corpus with a real working-tree deletion.

    Indexed corpus with foo defined in foo.py + bar.py still calling it.
    Then deletes foo's definition in the working tree -- creates a real
    BREAK-RISK signal. Used to exercise the diff-source-axis paths
    without bumping into the W805-Z empty-corpus zero-survivors fixture.
    """
    proj = tmp_path / "populated_with_deletion"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "foo.py").write_text("def foo():\n    return 1\n")
    (src / "bar.py").write_text("from src.foo import foo\n\ndef bar():\n    return foo()\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    (src / "foo.py").write_text("# foo removed\n")
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash on degenerate diff-source paths.
# ---------------------------------------------------------------------------


class TestDiffSourceNoCrash:
    """Bogus diff-source paths must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_bogus_commit_range_no_crash(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """Bogus commit-range: non-empty stdout, no exception."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        # No --ci -> exit 0 even on git_error.
        assert result.exit_code == 0, (
            f"bogus commit-range without --ci must exit 0; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus-range"

    def test_bogus_base_ref_pr_no_crash(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """--source pr with bogus base-ref: non-empty stdout, no exception."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--source", "pr", "--base-ref", "nonexistent_branch"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"--source pr with bogus base-ref must exit 0 without --ci; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus base-ref"


# ---------------------------------------------------------------------------
# W607-J orthogonality -- the SUBPROCESS-axis warnings_out marker IS expected
# to fire on the diff-source-failure path. The W805-AAAA axis pins what's
# MISSING beyond that (the state/resolution closed-enum). These tests are
# guard-rails that W805-AAAA's fix doesn't accidentally delete W607-J's
# warnings_out lineage marker.
# ---------------------------------------------------------------------------


class TestW607JOrthogonality:
    """W607-J's ``delete_check_git_diff_failed:`` marker IS expected on
    the bogus-diff-source path. These tests pin the orthogonality: the
    subprocess-degrade lineage stays, AND the W805-AAAA closed-enum
    disclosure must be added on top -- not as a replacement."""

    def test_w607_j_marker_present_on_bogus_range(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """W607-J's warnings_out marker fires on bogus commit range."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        top_wo = data.get("warnings_out") or []
        assert any(m.startswith("delete_check_git_diff_failed:") for m in top_wo), (
            f"W607-J orthogonality: expected delete_check_git_diff_failed: marker on bogus commit range; got {top_wo!r}"
        )

    def test_w607_j_marker_present_on_bogus_pr_base_ref(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """W607-J marker fires on --source pr with bogus base-ref."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--source", "pr", "--base-ref", "nonexistent_branch"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        top_wo = data.get("warnings_out") or []
        # On --source pr, the source field in the marker is 'pr'.
        assert any("delete_check_git_diff_failed:" in m and "source='pr'" in m for m in top_wo), (
            f"W607-J orthogonality: expected delete_check_git_diff_failed: "
            f"marker with source='pr' on bogus base-ref; got {top_wo!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestBogusCommitRangeResolutionDisclosure:
    """The bogus-commit-range path emits ``git_error: "git_error"`` with
    NO ``summary.state`` and NO ``summary.resolution`` closed-enum
    disclosure. An agent acting on the verdict cannot distinguish a
    user-fixable typo from an operational failure."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAA REAL BUG: src/roam/commands/cmd_delete_check.py:296-343 "
            "(the ``if git_err is not None:`` branch) emits "
            '``partial_success: true`` + ``git_error: "git_error"`` but NO '
            "``summary.state`` closed-enum disclosure. Two distinct USER "
            "errors (bogus --commit-range, bogus --source pr --base-ref) "
            "collapse into the same generic ``git_error`` sentinel. "
            "Pattern-1-V-D resolution-disclosure gap -- an agent cannot "
            "switch on the failure class without text-matching the verdict "
            "string. Pinned strict; graduates when the envelope adds "
            "``summary.state`` with a closed-enum value (e.g. "
            "``git_not_available`` / ``git_timeout`` / ``unknown_ref`` / "
            "``git_error``) that mirrors the existing ``_GIT_*`` module "
            "constants."
        ),
    )
    def test_bogus_commit_range_emits_state(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """Bogus commit-range path must emit ``summary.state``."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        state = summary.get("state")
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-AAAA Pattern-1-V-D: bogus-commit-range path must emit "
            f"summary.state to distinguish user typo from operational "
            f"failure; got {state!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAA REAL BUG: the same code path emits no "
            "``summary.resolution`` field on the bogus-diff-source path. "
            "Pattern-1-V-D contract: when a command resolves a target "
            "through a fallback chain (diff source -> git subprocess -> "
            "non-zero return), disclose the resolution state via a "
            "``resolution`` field on the envelope. The current envelope "
            "lacks both ``state`` AND ``resolution`` -- the agent has only "
            "``partial_success: true`` + a text verdict to switch on. "
            "Pinned strict so a future cleanup adding "
            "``resolution: 'unresolved_diff_source'`` graduates to PASS."
        ),
    )
    def test_bogus_commit_range_emits_resolution(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """Bogus commit-range path must emit ``summary.resolution``."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        resolution = summary.get("resolution")
        assert resolution is not None and isinstance(resolution, str) and resolution.strip(), (
            f"W805-AAAA Pattern-1-V-D: bogus-commit-range path must emit "
            f"summary.resolution to disclose the fallback-chain state; "
            f"got {resolution!r}"
        )


class TestBogusPrBaseRefResolutionDisclosure:
    """``--source pr`` with a bogus base-ref is the second user-error
    class that collapses into the generic ``git_error`` sentinel. Same
    Pattern-1-V-D gap as the bogus-commit-range path."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAA REAL BUG (mirror): --source pr with a bogus "
            '--base-ref emits the same generic ``git_error: "git_error"`` '
            "as the bogus commit-range path. Pattern-1-V-D resolution-"
            "disclosure gap: an agent running ``delete-check --source pr "
            "--base-ref <typo>`` should receive a state disclosure that "
            "names ``unknown_base_ref`` (or equivalent closed-enum), not "
            "the same generic sentinel used for git-binary-missing. "
            "Pinned strict."
        ),
    )
    def test_bogus_pr_base_ref_emits_state(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """--source pr with bogus base-ref must emit ``summary.state``."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--source", "pr", "--base-ref", "nonexistent_branch"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        state = summary.get("state")
        assert state is not None and isinstance(state, str) and state.strip(), (
            f"W805-AAAA Pattern-1-V-D: --source pr with bogus base-ref must emit summary.state; got {state!r}"
        )


# ---------------------------------------------------------------------------
# CI exit-5 fail-loud -- already correct on the diff-source path
# (W607-J's contract). Positive regression so a future cleanup doesn't
# accidentally drop the fail-loud behavior.
# ---------------------------------------------------------------------------


class TestCiExit5OnDiffSourceFailure:
    """Confirm the existing fail-loud behavior on the diff-source path:
    ``--ci`` + bogus commit-range / base-ref exits 5 (gate-failure).
    Pre-existing contract (W607-J + CP45/CP46); positive regression."""

    def test_ci_exit_5_on_bogus_commit_range(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """--ci + bogus commit-range exits 5 (cannot gate => fail loud)."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD", "--ci"],
            cwd=populated_corpus_with_deletion,
        )
        EXIT_GATE_FAILURE = 5
        assert result.exit_code == EXIT_GATE_FAILURE, (
            f"--ci + bogus commit-range must exit {EXIT_GATE_FAILURE} "
            f"(W607-J fail-loud contract); got {result.exit_code}\n{result.output}"
        )

    def test_ci_exit_5_on_bogus_pr_base_ref(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """--ci + bogus PR base-ref exits 5 (cannot gate => fail loud)."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--source", "pr", "--base-ref", "nonexistent_branch", "--ci"],
            cwd=populated_corpus_with_deletion,
        )
        EXIT_GATE_FAILURE = 5
        assert result.exit_code == EXIT_GATE_FAILURE, (
            f"--ci + bogus PR base-ref must exit {EXIT_GATE_FAILURE}; got {result.exit_code}\n{result.output}"
        )


# ---------------------------------------------------------------------------
# Positive regression -- clean diff sources still produce real verdicts.
# Guards against an over-correcting fix-forward.
# ---------------------------------------------------------------------------


class TestCleanCorpusRealVerdict:
    """Sanity: a clean diff-source on the populated-corpus fixture still
    produces a real BREAK-RISK verdict (foo deleted, bar.py still
    references it)."""

    def test_clean_working_diff_break_risk(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """Default --source working on real deletion -> BREAK-RISK."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check"],
            cwd=populated_corpus_with_deletion,
            json_mode=True,
        )
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        assert summary.get("overall") == "BREAK-RISK", (
            f"genuine BREAK-RISK corpus must emit overall=BREAK-RISK; got {summary.get('overall')!r}"
        )
        assert summary.get("break_risk", 0) >= 1, (
            f"genuine BREAK-RISK corpus must have >=1 break_risk count; got {summary.get('break_risk')!r}"
        )

    def test_clean_working_diff_ci_exit_5(self, cli_runner, populated_corpus_with_deletion, monkeypatch):
        """Real BREAK-RISK + --ci must exit 5."""
        monkeypatch.chdir(populated_corpus_with_deletion)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--ci"],
            cwd=populated_corpus_with_deletion,
        )
        EXIT_GATE_FAILURE = 5
        assert result.exit_code == EXIT_GATE_FAILURE, (
            f"real BREAK-RISK + --ci must exit {EXIT_GATE_FAILURE}; got {result.exit_code}\n{result.output}"
        )
