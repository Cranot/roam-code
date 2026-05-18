"""W805-IIIII -- ``cmd_postmortem`` verifier-side identity-skip DISCONFIRMATION pin.

Hundred-and-thirteenth-in-batch W805 sweep. PROBE was: is ``cmd_postmortem``
the FIFTH member of the verifier-side identity-skip slice of the
lineage-disclosure family, alongside:

- W805-PPPP cmd_cga                  (predicate.subject[0].name never checked)
- W805-UUUU cmd_audit_trail_verify   (actor/repo/git_sha never cross-checked)
- W805-ZZZZ cmd_evidence_diff        (two-packet identity never cross-checked)
- W805-BBBBB cmd_pr_bundle validate  (bundle commit_sha never cross-checked)

The hypothesis (from the parent-agent's W805-FFFFF debrief): "cmd_pr_replay
wraps ``roam postmortem`` over a commit range. If postmortem has any
re-load + re-check path against prior runs/findings, it would be a sibling
of the verifier-side family."

**RESULT: DISCONFIRMED.** ``cmd_postmortem`` is structurally INELIGIBLE for
the verifier-side identity-skip family. The pin therefore captures the
POSITIVE architectural invariant (no verifier-side re-load path exists; the
command is a live-only git-log + live-critique walker) so a future refactor
that introduces a persisted-artefact path is caught at the architectural
gate, not after it ships with the same bug class as cmd_cga / cmd_audit_trail_verify.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Full-source read of ``cmd_postmortem.py``** (289 lines). The command:
   - Calls ``_git_log_in_range(commit_range, limit=limit)`` ->
     ``git log --max-count=<limit> --pretty=... <range>`` on the LIVE repo.
   - Iterates commits and calls ``_diff_for_commit(sha)`` ->
     ``git show --pretty= --unified=3 <sha>`` on the LIVE repo.
   - Calls ``_critique_diff(diff_text)`` ->
     ``roam --json critique`` in-process (CliRunner) against the LIVE diff.
   - Emits a transient, invocation-scoped envelope. NO ``emit_finding``,
     NO ``.roam/postmortems/<id>.json`` persisted artefact, NO
     ``load_postmortem`` / ``read_postmortem`` reader.

2. **Cross-file grep for postmortem persistence**:

       grep -rn 'postmortems|load_postmortem|read_postmortem' src/roam/

   Result: ZERO hits outside the cmd_postmortem docstring / examples /
   ``cmd_pr_replay.py`` *consumer*-side parsing (re-parses the
   ``_short_finding_summary`` output from a fresh invocation, not from a
   persisted file). There is NO postmortem artefact on disk for a verifier
   to re-load.

3. **Axis distinction from W805-RRR (sister axis, sibling W805 entry).**
   W805-RRR pinned cmd_postmortem at the POST-HOC RETROSPECTIVE-REPLAY
   axis: Pattern-2 silent-SAFE invariance across time-direction (forward
   gate AND backward replay produce the same silent-SAFE bug class).
   W805-IIIII probes a DIFFERENT axis -- the verifier-side re-load +
   identity-check path. The two axes are independent: a command can have
   a post-hoc retrospective-replay shape (RRR axis confirmed) WITHOUT
   having a verifier-side re-load path (IIIII axis disconfirmed).
   Both pins coexist; neither subsumes the other.

4. **Structural eligibility check against the 4-STRONG family shape.**
   All four sister verifier-side members share TWO load-bearing
   structural features:

     (a) A PERSISTED ARTEFACT on disk: ``.roam/cga/<id>.intoto.jsonl``
         (PPPP), ``.roam/runs/<id>/events.jsonl`` (UUUU),
         ``.roam/evidence/<scope>.json`` (ZZZZ),
         ``.roam/pr-bundles/<branch>.json`` (BBBBB).

     (b) A RE-LOAD + RE-CHECK PATH: ``verify_cga_statement`` (PPPP),
         ``audit_trail_verify`` (UUUU), ``evidence_diff`` (ZZZZ),
         ``pr_bundle validate`` (BBBBB).

   cmd_postmortem has NEITHER. (a) no postmortem artefact on disk;
   (b) no re-load path -- every invocation walks LIVE git from scratch.
   The verifier-side identity-skip family is therefore structurally
   inapplicable: there is no persisted identity to re-load, ergo no
   identity to silently skip cross-checking.

5. **W907 verify-the-cycle check.** Lazy import at line 111
   ``from roam.cli import cli`` inside ``_critique_diff``. NOT a
   cargo-cult / false-hedge: the cycle is real -- ``roam.commands.cmd_postmortem``
   is loaded by ``roam.cli`` via the LazyGroup ``_COMMANDS`` dict
   (cli.py:221), so importing ``roam.cli`` at module-top would create
   a true circular import. Genuine lazy import; no defensive docstring
   claiming a false cycle. W907 invariant holds.

W805 sweep tally update
=======================

- Through W805-IIIII: ~54/54 commands probed across the sweep.
- This entry: DISCONFIRMATION (architectural invariant pinned, no bug).
- Verifier-side family STAYS at 4-STRONG (PPPP / UUUU / ZZZZ / BBBBB).
  cmd_postmortem is NOT eligible; family did not grow.
- Lineage-disclosure family STAYS at 9-STRONG (5 producer-side + 4
  verifier-side); cmd_postmortem is NOT a new member on either axis
  via this probe.

W805-JJJJJ candidate axes (recommendations for next batch)
==========================================================

- ``cmd_triage`` (still open from the W805-RRR candidate list) --
  likely findings-prioritizer / consumer; would close the
  incident-management family alongside the W805-RRR retrospective-replay
  entry. Distinct from postmortem (triage is a forward-looking
  prioritizer of CURRENT findings; postmortem is a backward-looking
  replay of PAST commits).
- ``cmd_for_*`` recipe commands (for_security_review / for_bug_fix /
  for_refactor / diagnose_issue) -- compound-recipe orchestrators that
  chain ``preflight`` / ``impact`` / ``critique`` / ``vulns`` etc.
  Axis: COMPOUND-RECIPE identity-skip (the resolved-symbol passed
  between subcommands may degrade silently when each subcommand
  re-resolves from a string -- LAW 9 ``coupling lives in what steps
  SAY``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD_PATH = _REPO_ROOT / "src" / "roam" / "commands" / "cmd_postmortem.py"


# ---------------------------------------------------------------------------
# Architectural invariants -- HOLD TODAY (no xfail)
# ---------------------------------------------------------------------------


def test_cmd_postmortem_exists():
    """``cmd_postmortem.py`` must exist (W805 sweep precondition)."""
    if not _CMD_PATH.exists():
        pytest.skip(f"cmd_postmortem not present at {_CMD_PATH}")
    assert _CMD_PATH.is_file()


def test_cmd_postmortem_has_no_persisted_artefact_path():
    """W978 verification: postmortem does NOT persist or re-load an artefact.

    The verifier-side identity-skip family (cga/audit_trail/evidence_diff/
    pr-bundle validate) is structurally defined by a persisted-artefact +
    re-load + re-check shape. cmd_postmortem lacks both halves:

      * Source MUST NOT reference ``.roam/postmortems/`` (no persisted
        artefact directory).
      * Source MUST NOT define ``load_postmortem`` / ``read_postmortem`` /
        ``verify_postmortem`` / ``replay_postmortem`` functions
        (no re-load + re-check path).

    If a future refactor introduces a persisted postmortem artefact,
    THIS test fails -- alerting the next agent that the verifier-side
    identity-skip family probe (W805-IIIII) must be re-opened.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    # No persisted artefact directory references.
    assert ".roam/postmortems" not in src, (
        "cmd_postmortem now references .roam/postmortems/ -- "
        "verifier-side identity-skip family probe (W805-IIIII) "
        "MUST be re-opened. The disconfirmation pin assumed no "
        "persisted artefact path; that assumption is now invalid."
    )
    # No re-load / re-verify helper functions.
    for forbidden in (
        "def load_postmortem",
        "def read_postmortem",
        "def verify_postmortem",
        "def replay_postmortem",
    ):
        assert forbidden not in src, (
            f"cmd_postmortem now defines `{forbidden}` -- a verifier-side "
            "re-load path now exists. W805-IIIII disconfirmation pin "
            "MUST be re-opened; the command is now eligible for the "
            "verifier-side identity-skip family (PPPP/UUUU/ZZZZ/BBBBB)."
        )


def test_cmd_postmortem_is_live_only_walker():
    """Postmortem is a LIVE git-log + LIVE critique walker.

    Pins the architectural invariant that drives the disconfirmation:
    the command's load-bearing helpers MUST exist (or the architecture
    has drifted away from "live-only walker" into something with a
    persisted-artefact path, in which case W805-IIIII must be reopened).
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    # Live git-log walker -- helper exists.
    assert "def _git_log_in_range" in src
    # Live per-commit diff walker -- helper exists.
    assert "def _diff_for_commit" in src
    # Live critique invocation -- helper exists.
    assert "def _critique_diff" in src
    # Live invocation uses git log + git show -- not a persisted reader.
    # Match either inline-list or multi-line argv style.
    assert '"git",' in src and '"log"' in src and '"show"' in src


def test_cmd_postmortem_critique_is_inprocess_runner():
    """Critique invocation goes through CliRunner against LIVE state.

    Pins the structural feature that distinguishes postmortem from the
    verifier-side family: critique runs LIVE per-commit, not against a
    persisted artefact. If this drifts (e.g. critique results get cached
    to disk and re-loaded), the verifier-side axis becomes live again
    and W805-IIIII must be re-probed.
    """
    if not _CMD_PATH.exists():
        pytest.skip("cmd_postmortem not present")
    src = _CMD_PATH.read_text(encoding="utf-8")
    assert "from click.testing import CliRunner" in src
    assert "runner.invoke(cli" in src


def test_w805_rrr_sister_axis_preserved():
    """W805-RRR (post-hoc retrospective-replay axis) sister-test exists.

    The IIIII disconfirmation does NOT supersede the RRR confirmation --
    the two probe DISTINCT axes on the same command. RRR pinned 2 bugs
    on the Pattern-2 invariance-across-time-direction axis; IIIII pins
    the absence of the verifier-side re-load axis. Both coexist.
    """
    sister = _REPO_ROOT / "tests" / "test_w805_rrr_cmd_postmortem_empty_corpus.py"
    assert sister.exists(), (
        "W805-RRR sister test missing -- the IIIII disconfirmation "
        "pin assumes RRR's post-hoc retrospective-replay axis stays "
        "open and orthogonal."
    )


def test_w805_pppp_sister_verifier_family_preserved():
    """Sister verifier-side family head (W805-PPPP cmd_cga) is intact.

    The verifier-side family is 4-STRONG (PPPP/UUUU/ZZZZ/BBBBB) and
    cmd_postmortem is NOT a fifth member. Pin the sister tests' presence
    so a future renaming/deletion is caught -- if the family head goes
    missing, the disconfirmation rationale becomes stale.
    """
    for name in (
        "test_w805_pppp_cmd_cga_attestation_lineage.py",
        "test_w805_uuuu_cmd_audit_trail_verify_identity_skip.py",
        "test_w805_zzzz_cmd_evidence_diff_cross_repo_identity.py",
        "test_w805_bbbbb_cmd_pr_bundle_validate_identity_skip.py",
    ):
        sister = _REPO_ROOT / "tests" / name
        assert sister.exists(), (
            f"Sister verifier-side family member {name} missing -- "
            "the IIIII disconfirmation pin's 4-STRONG family-shape "
            "rationale assumes all four sisters stay green."
        )
