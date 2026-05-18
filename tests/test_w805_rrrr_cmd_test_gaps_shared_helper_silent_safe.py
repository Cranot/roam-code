"""W805-RRRR -- shared-helper silent-SAFE probe on ``roam test-gaps``.

Ninety-sixth-in-batch W805 sweep. FOURTH potential strict consumer for
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
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper
    on the analogous-but-not-shared axis.

Family stood 3-strong on the strict ``get_changed_files`` axis at the
start of this probe (cmd_diff + cmd_pr_diff + cmd_attest). cmd_test_gaps
-- test-impact selection mapping changed symbols to missing test
coverage -- is the natural next inheritor.

W978 first-hypothesis: cmd_test_gaps is a strict shared-helper consumer
--------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_test_gaps.py`` head-to-tail:

  * Lines 22-26: ``from roam.commands.changed_files import
    get_changed_files, is_test_file, resolve_changed_to_db``. The
    import IS the shared helper used by cmd_diff (W805-EEEE),
    cmd_pr_diff (W805-JJJJ), and cmd_attest (W805-OOOO).
  * Line 268: ``diff_files = get_changed_files(root)``. Called inside
    the ``if changed:`` branch. Bare call -- no ``commit_range`` /
    ``staged`` / ``pr`` parameters. cmd_test_gaps does NOT take a
    positional commit-range argument, so the bogus-ref input surface
    is narrower than its sister commands; but the helper-failure axis
    (git missing, timeout, returncode != 0) still inherits silent-SAFE.
  * Lines 271-293: ``if not target_paths:`` branch emits
    ``verdict: "No changed files to analyze"`` with NO ``state`` field
    and NO ``partial_success`` field -- the SAME failure shape on both
    "no --changed flag passed" AND "--changed passed but helper
    returned []" paths.

W978 finding: CONFIRMED. cmd_test_gaps inherits the W805-EEEE / W805-JJJJ
silent-SAFE shape via the shared helper. This is the FOURTH strict
consumer; the shared-helper family on the ``get_changed_files`` axis
elevates from 3-strong to 4-STRONG (cmd_diff + cmd_pr_diff + cmd_attest
+ cmd_test_gaps). The pattern is now FULLY STRUCTURAL: any consumer of
``get_changed_files`` inherits silent-SAFE unless the helper is upgraded
to return ``(paths, error_kind)``.

Probe results (subprocess against a clean indexed project)
----------------------------------------------------------

* ``roam --json test-gaps --changed`` on clean tree (uncommitted):
  exit 0, ``verdict: "No changed files to analyze"``,
  NO ``state`` field, NO ``partial_success`` true (it's ``False``),
  NO ``resolution`` field, NO ``git_error`` field. Output is byte-
  identical (on machine-state fields) to:

* ``roam --json test-gaps`` (no args, no --changed):
  exit 0, same verdict, same envelope -- agents reading machine-
  state fields cannot distinguish "user forgot --changed" from
  "--changed was passed but git failed silently" from "--changed was
  passed and the tree really is clean".

Strictly-more-severe than W805-OOOO cmd_attest
----------------------------------------------

cmd_test_gaps's degraded-resolution path is STRICTLY MORE SEVERE than
its three sister consumers:

  * cmd_diff (W805-EEEE): ``state: "no_changes"`` present.
  * cmd_pr_diff (W805-JJJJ): NO ``state`` field on the no-changes
    path -- the strictly-worst shape observed in the W805 sweep
    BEFORE this probe.
  * cmd_attest (W805-OOOO): ``state: "no_changes"`` + ``safe_to_merge:
    null`` -- best disclosure of the family.
  * cmd_test_gaps (W805-RRRR): NO ``state``, NO ``safe_to_merge``,
    ``partial_success: False``. The ``partial_success: False`` is
    actively misleading (Pattern-2 silent-fallback contract: the
    helper returning [] from a degraded resolution IS a partial
    success the envelope should disclose). Same severity tier as
    cmd_pr_diff, with the added Pattern-2 false-False signal.

The xfail-strict pins below match the W805-OOOO + W805-JJJJ shape
combined: the bogus / degraded path must emit a closed-enum ``state``
distinct from the clean-tree no-args path, OR a non-empty
``resolution`` field. Today both paths share the SAME envelope so the
pins are RED until the shared helper returns ``(paths, error_kind)``.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_test_gaps.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import``
case-insensitive): NO matches. cmd_test_gaps is a thin orchestrator
(reverse-edge BFS + severity classification + envelope emission) with
all imports at module scope and no defensive lazy-import hedges. Clean
on W907.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff (shared-helper consumer).
  * W805-JJJJ: cmd_pr_diff (shared-helper consumer) -- 2nd strict
    consumer.
  * W805-OOOO: cmd_attest (shared-helper consumer) -- 3rd strict
    consumer.
  * W805-MMMM: cmd_ws (DISCONFIRMED).
  * W805-AAAA: cmd_delete_check (independent helper).

After this probe:
  * W805-RRRR: cmd_test_gaps CONFIRMED (shared-helper consumer) --
    FOURTH strict ``get_changed_files`` consumer.
  * Shared-helper family elevates to 4-STRONG on the strict
    ``get_changed_files`` axis (cmd_diff + cmd_pr_diff + cmd_attest +
    cmd_test_gaps). Pattern is now FULLY STRUCTURAL. Total family
    across all axes is 5-strong (counting cmd_delete_check on the
    independent-but-analogous axis).

W805 sweep update
-----------------

W805 sweep yield ~50/50 (this probe = 50th). Strict-consumer family
is now fully structural (4-strong). Future fix: ``get_changed_files``
should be upgraded to ``(paths, error_kind)`` -- a single change
atomically unblocks FOUR consumers (cmd_diff, cmd_pr_diff, cmd_attest,
cmd_test_gaps).

Next W805 sweep candidate (W805-SSSS)
-------------------------------------

Per the canonical strict-consumer list at W805-MMMM (19 modules),
remaining unprobed candidates include: cmd_adversarial, cmd_boundary,
cmd_why_slow, cmd_verify, cmd_syntax_check, cmd_suggest_reviewers,
cmd_coupling, cmd_affected_tests, cmd_affected, cmd_plan,
cmd_orchestrate (W805-DDDD scope but NOT shared-helper axis).
W805-SSSS candidate: cmd_affected_tests -- like test-gaps, it
selects tests on a changed-files boundary; natural inheritor of the
family-4-strong silent-SAFE shape.
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

    Used as the baseline -- the W805-RRRR bug is that a --changed
    invocation on a clean tree (helper returns []) should NOT be
    byte-identical to a no-args invocation (target_paths empty from the
    start) on machine-state fields.
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
# W978 verification -- cmd_test_gaps actually consumes get_changed_files.
# This test asserts the source-level contract that founds the W805-RRRR
# probe. If cmd_test_gaps is refactored to a different helper, this test
# graduates the W805-RRRR pin to "not applicable" rather than letting
# the bug class hide behind a stale assertion.
# ---------------------------------------------------------------------------


class TestCmdTestGapsConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_test_gaps is a confirmed
    consumer of ``get_changed_files`` from
    ``src/roam/commands/changed_files.py``. This is the source-level
    invariant that elevates W805-RRRR from a coincidental shape match
    to a structural shared-helper class member."""

    def test_cmd_test_gaps_consumes_get_changed_files(self):
        """Source-level check: cmd_test_gaps imports + calls get_changed_files.

        Fails if a refactor moves cmd_test_gaps onto a different helper.
        At that point the W805-RRRR pin is structurally stale and the
        new helper must be re-audited for the same bug class.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_test_gaps.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-RRRR W978-precondition: cmd_test_gaps must import "
            "from roam.commands.changed_files; if this changed, re-audit "
            "the shared-helper family membership."
        )
        assert "get_changed_files" in src, (
            "W805-RRRR W978-precondition: cmd_test_gaps must reference "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )
        assert "get_changed_files(root" in src, (
            "W805-RRRR W978-precondition: cmd_test_gaps must CALL "
            "get_changed_files(root, ...); if the call site moved, "
            "re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash on degenerate diff-source paths.
# Guard-rail: any future W805-RRRR fix must not reintroduce the
# empty-stdout crash class while adding disclosure on top.
# ---------------------------------------------------------------------------


class TestTestGapsSourceNoCrash:
    """Degenerate invocation modes must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_no_args_no_changed_flag_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """No args, no --changed: non-empty stdout, parseable JSON."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"no-args invocation must exit 0; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on no-args"
        data = parse_json_output(result, "test-gaps")
        assert isinstance(data, dict)

    def test_changed_flag_on_clean_tree_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """``--changed`` on clean tree: non-empty stdout, parseable JSON."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps", "--changed"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on --changed clean"
        data = parse_json_output(result, "test-gaps")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# REAL BUG pinned strict.
#
# cmd_test_gaps inherits silent-SAFE from get_changed_files. The envelope
# emits ``verdict: "No changed files to analyze"`` with NO ``state``
# field, NO ``resolution`` field, NO ``git_error`` field, and
# ``partial_success: False`` on BOTH the no-args AND the --changed-on-
# clean-tree (helper returned []) paths. Same class as W805-JJJJ
# cmd_pr_diff -- arguably worse due to the actively-misleading
# ``partial_success: False``.
# ---------------------------------------------------------------------------


class TestChangedFlagStateDisclosure:
    """The --changed-on-clean-tree path produces an envelope
    indistinguishable from a no-args invocation on every machine-state
    field. There is no closed-enum disclosure separating the two paths."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-RRRR REAL BUG: src/roam/commands/cmd_test_gaps.py:271-293 "
            "(the ``if not target_paths:`` branch downstream of "
            '``get_changed_files``) emits ``verdict: "No changed files to '
            'analyze"`` with NO ``state`` field and ``partial_success: '
            "False`` on the --changed-on-clean-tree path -- byte-identical "
            "to the no-args path on every machine-state field. The root "
            "cause is ``src/roam/commands/changed_files.py:142,145`` "
            "swallowing ``returncode != 0`` / FileNotFoundError / "
            "TimeoutExpired into an empty list. Pattern-1-V-D silent-"
            "success-on-degraded-resolution. FOURTH strict shared-helper "
            "consumer; FAMILY IS NOW 4-STRONG STRUCTURAL on the "
            "get_changed_files axis (cmd_diff + cmd_pr_diff + cmd_attest "
            "+ cmd_test_gaps). Pinned strict; graduates when the --changed "
            "path emits ``state`` with a closed-enum value distinct from "
            "the no-args path -- ideally atomically with W805-EEEE / "
            "W805-JJJJ / W805-OOOO graduation when the shared helper is "
            "upgraded to ``(paths, error_kind)``."
        ),
    )
    def test_changed_flag_emits_state_distinct_from_no_args(self, cli_runner, clean_indexed_project, monkeypatch):
        """--changed path must emit ``summary.state`` distinct from no-args."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps", "--changed"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "test-gaps")
        summary = data["summary"]
        state = summary.get("state")
        # The bug: state is missing -- no machine-state field to tell
        # "user passed --changed and helper failed silently" from
        # "user did not pass --changed".
        assert state and isinstance(state, str) and state.strip(), (
            f"W805-RRRR Pattern-1-V-D: --changed-on-clean-tree path must "
            f"emit a ``summary.state`` field disclosing the "
            f"degraded-resolution branch (helper returned []); "
            f"got {state!r}"
        )


class TestChangedFlagResolutionDisclosure:
    """Mirror axis: a --changed invocation where the helper returns []
    must emit a closed-enum ``resolution`` field, since this IS a
    degraded-resolution path under the shared helper."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-RRRR REAL BUG (resolution axis): --changed-on-clean-tree "
            "path emits no ``resolution`` field. Pattern-1-V-D contract "
            "requires AT LEAST ONE closed-enum disclosure (state OR "
            "resolution) on the degraded-resolution path. Pinned strict; "
            "graduates when the envelope distinguishes --changed-empty "
            "from no-args on either field."
        ),
    )
    def test_changed_flag_emits_resolution_or_state(self, cli_runner, clean_indexed_project, monkeypatch):
        """--changed-on-clean must emit ``summary.resolution`` OR ``summary.state``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps", "--changed"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "test-gaps")
        summary = data["summary"]
        resolution = summary.get("resolution")
        state = summary.get("state")
        assert (resolution and isinstance(resolution, str) and resolution.strip()) or (
            state and isinstance(state, str) and state.strip()
        ), (
            f"W805-RRRR Pattern-1-V-D: --changed-on-clean path must emit "
            f"summary.resolution OR summary.state; "
            f"got resolution={resolution!r} state={state!r}"
        )


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_test_gaps inherits the same silent-
    SAFE shape as cmd_diff, cmd_pr_diff, and cmd_attest via the shared
    ``get_changed_files`` helper. FOURTH strict consumer -- elevates the
    family to 4-STRONG STRUCTURAL. Pins the inheritance so a fix to the
    shared helper unblocks ALL FOUR consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-RRRR FAMILY-CONFIRMATION: cmd_test_gaps's --changed-on-"
            "clean path emits no ``git_error`` field -- the same gap "
            "W805-EEEE pins on cmd_diff, W805-JJJJ pins on cmd_pr_diff, "
            "and W805-OOOO pins on cmd_attest. The shared helper "
            "``src/roam/commands/changed_files.py:131-146`` returns "
            "an empty list on three distinct failure classes "
            "(returncode != 0, FileNotFoundError, TimeoutExpired). All "
            "FOUR consumers (cmd_diff, cmd_pr_diff, cmd_attest, "
            "cmd_test_gaps) inherit silent-SAFE -- the family is now "
            "4-STRONG STRUCTURAL. Pinned strict; graduates when "
            "``get_changed_files`` returns a ``(paths, error_kind)`` "
            "tuple and cmd_test_gaps surfaces ``summary.git_error`` on "
            "the failure branch."
        ),
    )
    def test_changed_flag_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """--changed-on-clean must emit ``summary.git_error`` distinct from no-args."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["test-gaps", "--changed"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "test-gaps")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-RRRR: --changed-on-clean path must emit "
            f"summary.git_error distinct from no-args (which has no git "
            f"interaction); got {git_error!r}"
        )


class TestNoArgsDistinctFromChangedEmpty:
    """Pattern-2 invariant: a no-args invocation and a --changed-on-
    clean-tree invocation MUST produce distinguishable envelopes. Today
    they are byte-identical on every machine-state field cmd_test_gaps
    emits."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-RRRR REAL BUG (invariant): no-args envelope and "
            "--changed-on-clean envelope are byte-identical on every "
            "machine-state field (state, resolution, git_error, "
            "partial_success, total_gaps). Pattern-2 silent-fallback "
            "contract violated: the --changed path took a git-diff "
            "round-trip that the no-args path did not; the envelope "
            "should disclose that the helper ran and returned []. "
            "Pinned strict; graduates when the two envelopes differ on "
            "at least one closed-enum machine-state field."
        ),
    )
    def test_no_args_distinct_from_changed_empty(self, cli_runner, clean_indexed_project, monkeypatch):
        """No-args envelope must differ from --changed-on-clean envelope on a machine-state field."""
        monkeypatch.chdir(clean_indexed_project)
        no_args_result = invoke_cli(
            cli_runner,
            ["test-gaps"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        changed_result = invoke_cli(
            cli_runner,
            ["test-gaps", "--changed"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        no_args_summary = parse_json_output(no_args_result, "test-gaps")["summary"]
        changed_summary = parse_json_output(changed_result, "test-gaps")["summary"]

        machine_state_fields = (
            "state",
            "resolution",
            "git_error",
            "partial_success",
        )
        differing = [f for f in machine_state_fields if no_args_summary.get(f) != changed_summary.get(f)]
        assert differing, (
            f"W805-RRRR Pattern-2: no-args and --changed-on-clean envelopes "
            f"must differ on at least one machine-state field "
            f"({machine_state_fields}); got identical values "
            f"no_args={ {f: no_args_summary.get(f) for f in machine_state_fields} } "
            f"changed={ {f: changed_summary.get(f) for f in machine_state_fields} }"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-checks -- W805-EEEE + W805-JJJJ + W805-OOOO
# invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper (which would graduate W805-EEEE,
# W805-JJJJ, W805-OOOO, and W805-RRRR atomically) MUST NOT perturb the
# clean-tree no-changes envelopes the prior sister commands emit.
# ---------------------------------------------------------------------------


class TestW805OoooInvariantsPreserved:
    """Sister cross-check: cmd_attest's W805-OOOO no-changes envelope
    shape is preserved. The clean-tree branch still emits
    ``state: "no_changes"`` + ``safe_to_merge: null``."""

    def test_attest_clean_tree_still_emits_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_attest clean tree still emits ``state: "no_changes"``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        # The pre-W805-OOOO contract: clean tree IS state=no_changes
        # with safe_to_merge=null. Must stay after any future
        # shared-helper fix.
        assert summary.get("state") == "no_changes", (
            f"W805-RRRR sister cross-check: cmd_attest clean-tree must "
            f"still emit state=no_changes; got {summary.get('state')!r}"
        )
        assert summary.get("safe_to_merge") is None, (
            f"W805-RRRR sister cross-check: cmd_attest clean-tree must "
            f"still emit safe_to_merge=null; "
            f"got {summary.get('safe_to_merge')!r}"
        )


class TestW805JjjjInvariantsPreserved:
    """Sister cross-check: cmd_pr_diff's W805-JJJJ no-changes envelope
    shape is preserved. The clean-tree branch still emits the canonical
    no-changes verdict."""

    def test_pr_diff_clean_tree_still_emits_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_pr_diff clean tree still emits ``no change`` verdict."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "pr-diff")
        summary = data["summary"]
        assert "no change" in summary.get("verdict", "").lower(), (
            f"W805-RRRR sister cross-check: cmd_pr_diff clean-tree must "
            f"still emit ``no change`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
        assert summary.get("partial_success") is False


class TestW805EeeeInvariantsPreserved:
    """Sister cross-check: cmd_diff's W805-EEEE no-changes envelope shape
    is preserved. The clean-tree branch still emits state=no_changes."""

    def test_diff_clean_tree_state_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_diff clean tree still emits state=no_changes."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "diff")
        summary = data["summary"]
        assert summary.get("state") == "no_changes", (
            f"W805-RRRR sister cross-check: cmd_diff clean-tree must "
            f"still emit state=no_changes; got {summary.get('state')!r}"
        )
        assert summary.get("verdict") == "no changes"
        assert summary.get("partial_success") is False


# ---------------------------------------------------------------------------
# Positive regression -- clean test-gaps invocation still produces real
# verdicts. Guards against an over-correcting fix-forward.
# ---------------------------------------------------------------------------


class TestCleanTestGapsPositiveRegression:
    """Positive regression: no-args invocation still emits the canonical
    "no changed files" envelope. This is the pre-W805-RRRR contract --
    it must stay even after the --changed-on-clean path is disambiguated."""

    def test_no_args_still_emits_no_changes_verdict(self, cli_runner, clean_indexed_project, monkeypatch):
        """No-args still emits ``No changed files to analyze`` verdict."""
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
            f"Positive regression: no-args verdict must mention ``no changed files``; got {summary.get('verdict')!r}"
        )
        assert summary.get("total_gaps") == 0
