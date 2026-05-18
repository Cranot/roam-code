"""W805-VVVV -- shared-helper silent-SAFE probe on ``roam affected``.

Hundredth-in-batch W805 sweep. SIXTH potential strict consumer for
the shared-helper resolution-disclosure family on the
``get_changed_files`` axis.

Family lineage entering this probe:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via the shared
    helper returning ``[]`` on all failure classes.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE silent-SAFE
    (no ``state`` field at all).
  * W805-OOOO (cmd_attest) -- THIRD strict consumer; mild improvement
    (``state: "no_changes"`` + ``safe_to_merge: null``) but still
    inherits the silent-SAFE shape.
  * W805-RRRR (cmd_test_gaps) -- FOURTH strict consumer; actively-
    misleading ``partial_success: False`` on the bogus-ref branch.
  * W805-SSSS (cmd_affected_tests) -- FIFTH strict consumer; STRICTLY
    WORST (plain text in --json mode = Pattern-1 Variant C).
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper
    on the analogous-but-not-shared axis.

Family stood 5-strong on the strict ``get_changed_files`` axis at the
start of this probe. cmd_affected -- the forward-walk sibling of
cmd_affected_tests -- has TWO bare ``get_changed_files`` call sites
(lines 207, 209) and IS the natural sixth inheritor.

W978 first-hypothesis: cmd_affected is a strict shared-helper consumer
----------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_affected.py`` head-to-tail:

  * Lines 23-27: ``from roam.commands.changed_files import
    get_changed_files, is_test_file, resolve_changed_to_db``. The
    import IS the shared helper used by all five prior strict
    consumers.
  * Line 207: ``changed = get_changed_files(root)`` inside the
    ``if use_changed:`` branch (working-tree mode, --changed flag).
    Bare call -- no error-channel parameters.
  * Line 209: ``changed = get_changed_files(root,
    commit_range=f"{base_ref}..HEAD")`` inside the ``else`` branch
    (commit-range mode, --base flag). Bare call. Bogus-ref input
    surface IS open here -- ``--base totally-bogus-ref-99`` will be
    silently swallowed by the helper's returncode != 0 path.
  * Lines 211-234: ``if not changed:`` branch DOES emit a JSON
    envelope in --json mode (unlike cmd_affected_tests's plain-text
    W805-SSSS-strictly-worst shape). Verdict: ``"No changes
    detected"``. No ``state`` / ``git_error`` / ``resolution`` field
    disclosing the helper-failure class. This is the W805-EEEE / W805-
    RRRR shape, NOT the W805-SSSS strictly-worst shape.

W978 finding: CONFIRMED + W805-EEEE/RRRR-SHAPE-MATCH. cmd_affected
inherits the W805-EEEE / W805-JJJJ / W805-OOOO / W805-RRRR silent-SAFE
shape via the shared helper. The shape is the canonical "emits JSON
envelope but doesn't disclose the helper-failure class" form (NOT the
W805-SSSS strictly-worse "plain text in --json mode" form). This is
the SIXTH strict consumer; the shared-helper family on the
``get_changed_files`` axis elevates from 5-STRONG to 6-STRONG
STRUCTURAL.

Probe results (CliRunner against an indexed clean tree)
-------------------------------------------------------

* ``roam --json affected`` on clean tree (default, line 209 call site):
  exit 0, JSON envelope emitted with ``summary.verdict = "No changes
  detected"`` + ``summary.state = None`` + ``summary.git_error = None``.

* ``roam --json affected --base totally-bogus-ref-99`` (line 209,
  bogus commit range): exit 0, IDENTICAL ``"No changes detected"``
  verdict. Bogus-ref is indistinguishable from clean-tree -- the
  silent-SAFE inheritance is structurally confirmed.

* ``roam --json affected --changed`` (line 207, working-tree mode):
  exit 0, IDENTICAL ``"No changes detected"`` verdict. Both call sites
  inherit silent-SAFE.

Shape comparison with sister consumers
--------------------------------------

cmd_affected's degraded-resolution shape matches the W805-EEEE /
W805-RRRR severity band (NOT strictly-worst):

  * cmd_diff (W805-EEEE): JSON envelope (``state: "no_changes"``).
  * cmd_pr_diff (W805-JJJJ): JSON envelope (no ``state``).
  * cmd_attest (W805-OOOO): JSON envelope (``state: "no_changes"``).
  * cmd_test_gaps (W805-RRRR): JSON envelope (no ``state``,
    ``partial_success: False``).
  * cmd_affected_tests (W805-SSSS): NO ENVELOPE (plain text -- strictly
    worst).
  * cmd_affected (W805-VVVV): JSON envelope (no ``state``, no
    ``git_error``, ``partial_success: False``). Matches W805-RRRR shape
    severity -- envelope present but degraded-resolution opaque.

The xfail-strict pins below match the W805-RRRR / W805-EEEE shape and
will graduate when the shared helper is upgraded to return
``(paths, error_kind)`` and cmd_affected surfaces ``summary.state``
or ``summary.git_error`` on the failure branch.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_affected.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import``
/ ``lazy.*import`` case-insensitive): NO matches. cmd_affected is a
thin orchestrator (forward-edge BFS + entry-point detection +
module grouping + envelope emission) with all imports at module
scope and no defensive lazy-import hedges. Clean on W907.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff -- 1st strict consumer.
  * W805-JJJJ: cmd_pr_diff -- 2nd strict consumer.
  * W805-OOOO: cmd_attest -- 3rd strict consumer.
  * W805-RRRR: cmd_test_gaps -- 4th strict consumer.
  * W805-SSSS: cmd_affected_tests -- 5th strict consumer (strictly worst).
  * W805-MMMM: cmd_ws (DISCONFIRMED).
  * W805-AAAA: cmd_delete_check (independent helper).

After this probe:
  * W805-VVVV: cmd_affected CONFIRMED (shared-helper consumer)
    -- SIXTH strict ``get_changed_files`` consumer. Matches W805-EEEE
    / W805-RRRR shape (envelope present + state opaque). TWO call
    sites (lines 207, 209) both inherit silent-SAFE.
  * Shared-helper family elevates to 6-STRONG STRUCTURAL on the
    strict ``get_changed_files`` axis (cmd_diff + cmd_pr_diff +
    cmd_attest + cmd_test_gaps + cmd_affected_tests + cmd_affected).
    Pattern is FULLY STRUCTURAL across six independent surfaces.
    Total family across all axes is 7-strong (counting cmd_delete_check
    on the independent-but-analogous axis).

W805 sweep update
-----------------

W805 sweep yield 51/51 (this probe = 52nd). Strict-consumer family
is now 6-STRONG fully structural. Future fix: ``get_changed_files``
should be upgraded to ``(paths, error_kind)`` -- a single change
atomically unblocks SIX consumers.

Next W805 sweep candidate (W805-WWWW)
-------------------------------------

Remaining unprobed strict-consumer candidates per the W805-MMMM
canonical list: cmd_adversarial, cmd_boundary, cmd_why_slow,
cmd_verify, cmd_syntax_check, cmd_suggest_reviewers, cmd_coupling,
cmd_plan. W805-WWWW candidate: cmd_adversarial -- next-most-likely
shared-helper consumer per the W805-MMMM strict-consumer ordering.
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
def clean_indexed_project(tmp_path):
    """Indexed project with a clean working tree (no uncommitted edits).

    The W805-VVVV bug is that an invocation against this clean tree
    (helper returns []) is INDISTINGUISHABLE from an invocation
    against a bogus ref (helper ALSO returns [] but for a different
    failure class). Both paths emit the same ``"No changes detected"``
    verdict with no closed-enum state/git_error disclosure.
    """
    proj = tmp_path / "clean-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\ndef main():\n    return greet('world')\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# W978 verification -- cmd_affected actually consumes get_changed_files
# at BOTH call sites (line 207 and line 209). Source-level contract.
# If cmd_affected is refactored to a different helper or merges the
# two call sites into one, this test graduates the W805-VVVV pin to
# "not applicable" rather than letting the bug class hide behind a
# stale assertion.
# ---------------------------------------------------------------------------


class TestCmdAffectedConsumesSharedHelperBothCallSites:
    """W978 first-hypothesis verification: cmd_affected is a confirmed
    consumer of ``get_changed_files`` at TWO distinct call sites
    (lines 207 + 209 per W805-SSSS forward-walk analysis). This is the
    source-level invariant that elevates W805-VVVV from a coincidental
    shape match to a structural shared-helper class member."""

    def test_cmd_affected_consumes_get_changed_files(self):
        """Source-level check: cmd_affected imports + calls get_changed_files.

        Fails if a refactor moves cmd_affected onto a different helper.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_affected.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-VVVV W978-precondition: cmd_affected must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files" in src, (
            "W805-VVVV W978-precondition: cmd_affected must reference "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )

    def test_cmd_affected_has_two_get_changed_files_call_sites(self):
        """Source-level check: cmd_affected has BOTH call sites preserved.

        TWO bare ``get_changed_files(...)`` invocations distinguish this
        consumer from cmd_affected_tests (which has one). Both call sites
        (working-tree mode, commit-range mode) inherit the silent-SAFE
        failure mode. If the refactor merges them into one branch, the
        W805-VVVV pin's "BOTH call sites" claim is structurally stale.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_affected.py").read_text(
            encoding="utf-8"
        )
        # Count "get_changed_files(" call sites — at least two distinct
        # invocations (one bare, one with commit_range=).
        call_count = src.count("get_changed_files(root")
        assert call_count >= 2, (
            f"W805-VVVV W978-precondition: cmd_affected must have at least "
            f"TWO ``get_changed_files(root...)`` call sites (one bare, one "
            f"with commit_range=). Got {call_count}; if collapsed to one, "
            f"re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Sanity / W978 second-hypothesis -- cmd_affected DOES emit a JSON
# envelope in --json mode on the no-changes branch (NOT the W805-SSSS
# plain-text-strictly-worst shape). This green-light test confirms the
# shape divergence from cmd_affected_tests up front.
# ---------------------------------------------------------------------------


class TestJsonEnvelopePresentInJsonMode:
    """W978 verification: cmd_affected --json on a clean tree emits a
    parseable JSON envelope (NOT plain text). Distinguishes the shape
    from W805-SSSS cmd_affected_tests strictly-worst shape."""

    def test_clean_tree_emits_parseable_json_envelope(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean tree must emit a parseable JSON envelope (not plain text)."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"clean tree must exit 0; got {result.exit_code}"
        data = parse_json_output(result, "affected")
        assert isinstance(data, dict), (
            f"W805-VVVV W978: cmd_affected --json on clean tree must emit "
            f"a parseable JSON envelope dict (NOT the W805-SSSS plain-text "
            f"strictly-worst shape); got {type(data).__name__}"
        )
        assert data.get("command") == "affected"


# ---------------------------------------------------------------------------
# Pattern-1 Variant D -- bogus-ref input MUST be distinguishable from
# clean-tree empty-diff. REAL BUG pinned strict. Today both paths emit
# the IDENTICAL ``"No changes detected"`` verdict (helper returncode
# != 0 swallowed silently).
# ---------------------------------------------------------------------------


class TestBogusRefDistinctFromEmptyDiff:
    """The bogus-ref path (helper returncode != 0) must be distinguishable
    from the clean-tree path (helper returns [] legitimately). Today
    both emit the IDENTICAL ``"No changes detected"`` verdict --
    Pattern-1 Variant D silent-fallback on a degraded resolution."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-VVVV REAL BUG: src/roam/commands/cmd_affected.py:209 "
            "(``get_changed_files(root, commit_range=f'{base_ref}..HEAD')`` "
            "in the ``else`` branch) inherits silent-SAFE from the shared "
            "helper at src/roam/commands/changed_files.py:131-146. When "
            "``--base totally-bogus-ref-99`` is passed, the helper "
            "swallows the returncode != 0 and returns []; cmd_affected's "
            "``if not changed:`` branch (lines 211-234) emits the same "
            "``'No changes detected'`` verdict as a legitimately-clean "
            "tree. Pattern-1 Variant D: degraded-resolution paths must "
            "be distinguishable from full-resolution-with-no-changes "
            "paths. SIXTH strict shared-helper consumer; family is now "
            "6-STRONG STRUCTURAL on the get_changed_files axis. Pinned "
            "strict; graduates when the bogus-ref verdict differs from "
            "the clean-tree verdict."
        ),
    )
    def test_bogus_ref_verdict_differs_from_clean_tree(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref verdict must differ from clean-tree verdict."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["affected"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_data = parse_json_output(clean_result, "affected")
        bogus_result = invoke_cli(
            cli_runner,
            ["affected", "--base", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_data = parse_json_output(bogus_result, "affected")
        clean_verdict = clean_data["summary"].get("verdict")
        bogus_verdict = bogus_data["summary"].get("verdict")
        assert clean_verdict != bogus_verdict, (
            f"W805-VVVV Pattern-1-V-D: bogus-ref verdict must differ from "
            f"clean-tree verdict; both got {clean_verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1-V-D state disclosure -- the ``if not changed:`` branch must
# emit a closed-enum ``state`` (or ``git_error``) field disclosing
# WHICH failure class drove the helper to return []. REAL BUG pinned
# strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The bogus-ref ``if not changed:`` branch must emit a closed-enum
    ``state`` field disclosing the degraded-resolution branch (helper
    returncode != 0 vs legitimately-empty diff)."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-VVVV REAL BUG (state axis): cmd_affected's "
            "``if not changed:`` branch (lines 211-234) emits a JSON "
            "envelope WITHOUT a ``summary.state`` field. The branch "
            "merges three distinct conditions: (1) legitimately-empty "
            "diff, (2) helper returncode != 0 (bogus ref / git not "
            "found), (3) FileNotFoundError / TimeoutExpired in the "
            "helper subprocess call. Pattern-1-V-D requires a closed-"
            "enum disclosure (state OR git_error OR resolution) on the "
            "degraded-resolution branch. Pinned strict; graduates when "
            "cmd_affected populates ``summary.state`` distinct between "
            "the no-changes and helper-failure classes."
        ),
    )
    def test_bogus_ref_emits_state_or_git_error(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.state`` or ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected", "--base", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "affected")
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-VVVV Pattern-1-V-D: bogus-ref path must emit a closed-"
            f"enum disclosure (summary.state OR summary.git_error OR "
            f"summary.resolution); got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Family-confirmation -- cmd_affected inherits silent-SAFE via the
# shared helper, the SIXTH strict consumer of the family. Pins the
# inheritance so a fix to the shared helper unblocks ALL SIX consumers
# atomically.
# ---------------------------------------------------------------------------


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_affected inherits the same silent-
    SAFE shape as cmd_diff, cmd_pr_diff, cmd_attest, cmd_test_gaps, and
    cmd_affected_tests via the shared ``get_changed_files`` helper.
    SIXTH strict consumer -- elevates the family to 6-STRONG STRUCTURAL.
    Pins the inheritance so a fix to the shared helper unblocks ALL SIX
    consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-VVVV FAMILY-CONFIRMATION: cmd_affected's bogus-ref "
            "``if not changed:`` branch emits a JSON envelope without "
            "any ``git_error`` field -- the same gap W805-EEEE pins on "
            "cmd_diff, W805-JJJJ pins on cmd_pr_diff, W805-OOOO pins on "
            "cmd_attest, W805-RRRR pins on cmd_test_gaps, and W805-SSSS "
            "pins on cmd_affected_tests. The shared helper "
            "``src/roam/commands/changed_files.py:131-146`` returns an "
            "empty list on three distinct failure classes (returncode "
            "!= 0, FileNotFoundError, TimeoutExpired). All SIX consumers "
            "(cmd_diff, cmd_pr_diff, cmd_attest, cmd_test_gaps, "
            "cmd_affected_tests, cmd_affected) inherit silent-SAFE -- "
            "the family is now 6-STRONG STRUCTURAL. Pinned strict; "
            "graduates when ``get_changed_files`` returns a "
            "``(paths, error_kind)`` tuple and cmd_affected surfaces "
            "``summary.git_error`` on the failure branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected", "--base", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "affected")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-VVVV: bogus-ref path must emit summary.git_error; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-checks -- W805-SSSS + W805-RRRR + W805-EEEE
# invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper MUST NOT perturb the clean-tree
# no-changes envelopes the prior sister commands emit.
# ---------------------------------------------------------------------------


class TestW805SsssInvariantsPreserved:
    """Sister cross-check: cmd_affected_tests's W805-SSSS no-args
    envelope shape is preserved. The no-args path still exits 1 with
    a usage hint mentioning TARGET or --staged."""

    def test_affected_tests_no_args_still_emits_usage_hint(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_affected_tests no-args still exits 1 with usage hint."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 1, (
            f"W805-VVVV sister cross-check: cmd_affected_tests no-args "
            f"must still exit 1 (usage error); got {result.exit_code}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert "target" in out.lower() or "staged" in out.lower(), (
            f"W805-VVVV sister cross-check: cmd_affected_tests no-args must mention TARGET or --staged; got {out!r}"
        )


class TestW805RrrrInvariantsPreserved:
    """Sister cross-check: cmd_test_gaps's W805-RRRR no-args envelope
    shape is preserved. The no-args path still emits the canonical
    ``no changed files to analyze`` verdict."""

    def test_test_gaps_no_args_still_emits_no_changes_verdict(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_test_gaps no-args still emits ``No changed files`` verdict."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "test-gaps")
        summary = data["summary"]
        assert "no changed files" in summary.get("verdict", "").lower(), (
            f"W805-VVVV sister cross-check: cmd_test_gaps no-args must "
            f"still emit ``no changed files`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
        assert summary.get("total_gaps") == 0
