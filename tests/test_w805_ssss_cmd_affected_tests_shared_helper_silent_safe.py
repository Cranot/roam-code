"""W805-SSSS -- shared-helper silent-SAFE probe on ``roam affected-tests``.

Ninety-seventh-in-batch W805 sweep. FIFTH potential strict consumer for
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
  * W805-RRRR (cmd_test_gaps) -- FOURTH strict consumer; STRICTLY MORE
    SEVERE than W805-OOOO with actively-misleading
    ``partial_success: False``.
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper
    on the analogous-but-not-shared axis.

Family stood 4-strong on the strict ``get_changed_files`` axis at the
start of this probe. cmd_affected_tests -- selects tests on the
changed-files boundary via ``--staged`` mode -- is the natural fifth
inheritor.

W978 first-hypothesis: cmd_affected_tests is a strict shared-helper consumer
---------------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_affected_tests.py`` head-to-tail:

  * Lines 11-15: ``from roam.commands.changed_files import
    get_changed_files, is_test_file, resolve_changed_to_db``. The
    import IS the shared helper used by all four prior strict
    consumers.
  * Line 317: ``changed = get_changed_files(root, staged=True)`` inside
    the ``if staged:`` branch. Bare call -- no error-channel parameters.
    The bogus-ref input surface is narrower than cmd_diff / cmd_pr_diff
    (no ``commit_range`` positional) but the helper-failure axis (git
    missing, timeout, returncode != 0) still inherits silent-SAFE.
  * Lines 318-323: ``if not changed:`` branch emits the plain-text
    ``click.echo("No staged changes found.")`` and ``return``. STRICTLY
    WORSE than the four sister consumers: cmd_affected_tests does NOT
    emit a JSON envelope at all on this path -- the ``--json`` flag is
    silently ignored. This is a Pattern-1 Variant C (empty-envelope /
    no structured envelope) compounded with the Pattern-1-V-D
    silent-fallback inheritance.

W978 finding: CONFIRMED + STRICTLY WORSE. cmd_affected_tests inherits
the W805-EEEE / W805-JJJJ / W805-OOOO / W805-RRRR silent-SAFE shape via
the shared helper AND violates the canonical Pattern-1-V-C rule (always
emit a structured envelope in JSON mode). This is the FIFTH strict
consumer; the shared-helper family on the ``get_changed_files`` axis
elevates from 4-strong to 5-STRONG. The pattern is now FULLY STRUCTURAL
on five independent command surfaces.

Probe results (CliRunner against an indexed clean tree)
-------------------------------------------------------

* ``roam --json affected-tests --staged`` on clean tree:
  exit 0, stdout = ``"No staged changes found.\n"`` (LITERAL PLAIN
  TEXT, no JSON envelope). The --json flag is silently ignored.

* ``roam --json affected-tests`` (no args, no --staged):
  exit 1, stdout = ``"Provide a TARGET symbol/file or use --staged.\n"``
  (also plain text). Two distinct failure shapes, neither structured.

Strictly-more-severe than W805-RRRR cmd_test_gaps
-------------------------------------------------

cmd_affected_tests's degraded-resolution path is STRICTLY MORE SEVERE
than all four prior sister consumers:

  * cmd_diff (W805-EEEE): ``state: "no_changes"`` present in envelope.
  * cmd_pr_diff (W805-JJJJ): JSON envelope emitted (no ``state``).
  * cmd_attest (W805-OOOO): JSON envelope (``state: "no_changes"``).
  * cmd_test_gaps (W805-RRRR): JSON envelope (no ``state``,
    ``partial_success: False``).
  * cmd_affected_tests (W805-SSSS): NO JSON ENVELOPE AT ALL on the
    --staged-on-clean path. Plain-text ``click.echo`` despite --json
    mode. This violates the LAW-1 "JSON envelope is the dominant
    variable" + Pattern-1 Variant C contract simultaneously.

The xfail-strict pins below match the W805-RRRR shape but add a strict
Pattern-1-V-C precondition: the path must emit ANY parseable JSON
envelope in --json mode before downstream state / resolution
disclosures can be asserted. Today the path emits plain text so the
JSON-envelope pin is RED until the path is upgraded to emit a structured
envelope.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_affected_tests.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import``
case-insensitive): NO matches. cmd_affected_tests is a thin
orchestrator (reverse-edge BFS + colocated-test detection + envelope
emission) with all imports at module scope and no defensive lazy-import
hedges. Clean on W907.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff -- 1st strict consumer.
  * W805-JJJJ: cmd_pr_diff -- 2nd strict consumer.
  * W805-OOOO: cmd_attest -- 3rd strict consumer.
  * W805-RRRR: cmd_test_gaps -- 4th strict consumer.
  * W805-MMMM: cmd_ws (DISCONFIRMED).
  * W805-AAAA: cmd_delete_check (independent helper).

After this probe:
  * W805-SSSS: cmd_affected_tests CONFIRMED (shared-helper consumer)
    -- FIFTH strict ``get_changed_files`` consumer. STRICTLY WORSE
    than the prior four: emits plain text in --json mode.
  * Shared-helper family elevates to 5-STRONG on the strict
    ``get_changed_files`` axis (cmd_diff + cmd_pr_diff + cmd_attest +
    cmd_test_gaps + cmd_affected_tests). Pattern is now FULLY
    STRUCTURAL across five independent surfaces. Total family across
    all axes is 6-strong (counting cmd_delete_check on the
    independent-but-analogous axis).

W805 sweep update
-----------------

W805 sweep yield ~50/50 (this probe = 51st). Strict-consumer family
is now fully structural (5-strong). Future fix: ``get_changed_files``
should be upgraded to ``(paths, error_kind)`` -- a single change
atomically unblocks FIVE consumers AND cmd_affected_tests additionally
needs its ``if not changed:`` branch upgraded to emit a JSON envelope
on --json mode.

Next W805 sweep candidate (W805-TTTT)
-------------------------------------

Per the canonical strict-consumer list at W805-MMMM (19 modules),
remaining unprobed candidates include: cmd_affected (cmd_affected_tests'
forward-walk sibling; ALSO consumes get_changed_files at lines 207 +
209), cmd_adversarial, cmd_boundary, cmd_why_slow, cmd_verify,
cmd_syntax_check, cmd_suggest_reviewers, cmd_coupling, cmd_plan.
W805-TTTT candidate: cmd_affected -- the direct forward-walk sibling
of cmd_affected_tests with TWO get_changed_files call sites (lines
207 + 209, both bare); natural sixth inheritor with the same shared-
helper failure-class blindness.
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

    Used as the baseline -- the W805-SSSS bug is that a --staged
    invocation on a clean tree (helper returns []) emits plain text
    even in --json mode, violating Pattern-1 Variant C.
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
# W978 verification -- cmd_affected_tests actually consumes get_changed_files.
# Source-level contract that founds the W805-SSSS probe. If
# cmd_affected_tests is refactored to a different helper, this test
# graduates the W805-SSSS pin to "not applicable" rather than letting
# the bug class hide behind a stale assertion.
# ---------------------------------------------------------------------------


class TestCmdAffectedTestsConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_affected_tests is a confirmed
    consumer of ``get_changed_files`` from
    ``src/roam/commands/changed_files.py``. This is the source-level
    invariant that elevates W805-SSSS from a coincidental shape match
    to a structural shared-helper class member."""

    def test_cmd_affected_tests_consumes_get_changed_files(self):
        """Source-level check: cmd_affected_tests imports + calls get_changed_files.

        Fails if a refactor moves cmd_affected_tests onto a different helper.
        At that point the W805-SSSS pin is structurally stale and the
        new helper must be re-audited for the same bug class.
        """
        src = (
            Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_affected_tests.py"
        ).read_text(encoding="utf-8")
        assert "from roam.commands.changed_files import" in src, (
            "W805-SSSS W978-precondition: cmd_affected_tests must import "
            "from roam.commands.changed_files; if this changed, re-audit "
            "the shared-helper family membership."
        )
        assert "get_changed_files" in src, (
            "W805-SSSS W978-precondition: cmd_affected_tests must reference "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )
        assert "get_changed_files(root" in src, (
            "W805-SSSS W978-precondition: cmd_affected_tests must CALL "
            "get_changed_files(root, ...); if the call site moved, "
            "re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- structured envelope required in --json mode.
# REAL BUG pinned strict (compound severity: Pattern-1-V-C +
# Pattern-1-V-D simultaneously).
#
# cmd_affected_tests's ``if not changed:`` branch at lines 318-323 emits
# the plain-text ``click.echo("No staged changes found.")`` and returns
# without honoring --json mode. This is the strictly-worst failure shape
# observed in the entire W805 sweep: no structured envelope AT ALL.
# ---------------------------------------------------------------------------


class TestStagedOnCleanEmitsJsonEnvelope:
    """The --staged-on-clean-tree path must emit a structured JSON
    envelope in --json mode (Pattern-1 Variant C). Today it emits plain
    text -- strictly worse than all four sister consumers."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-SSSS REAL BUG: src/roam/commands/cmd_affected_tests.py:"
            "318-323 (the ``if not changed:`` branch downstream of "
            "``get_changed_files(root, staged=True)``) emits "
            "``click.echo('No staged changes found.')`` as PLAIN TEXT "
            "without honoring --json mode. Pattern-1 Variant C: any "
            "JSON-mode invocation MUST emit a structured envelope. "
            "STRICTLY WORSE than W805-EEEE / W805-JJJJ / W805-OOOO / "
            "W805-RRRR which all emit a JSON envelope (with varying "
            "degrees of state-field disclosure). FIFTH strict shared-"
            "helper consumer; FAMILY IS NOW 5-STRONG STRUCTURAL on the "
            "get_changed_files axis. Pinned strict; graduates when the "
            "--staged-on-clean path emits a parseable JSON envelope in "
            "--json mode."
        ),
    )
    def test_staged_clean_tree_emits_parseable_json(self, cli_runner, clean_indexed_project, monkeypatch):
        """--staged-on-clean must emit a parseable JSON envelope in --json mode."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"--staged on clean tree must exit 0; got {result.exit_code}"
        data = parse_json_output(result, "affected-tests")
        assert isinstance(data, dict), (
            f"W805-SSSS Pattern-1-V-C: --staged-on-clean must emit a "
            f"parseable JSON envelope dict; got {type(data).__name__}"
        )


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# Mirrors the W805-RRRR / W805-JJJJ / W805-OOOO / W805-EEEE pins
# adapted for cmd_affected_tests's --staged surface. These will stay
# RED until the structured envelope is emitted AND the disclosure
# fields are populated.
# ---------------------------------------------------------------------------


class TestStagedStateDisclosure:
    """The --staged-on-clean-tree path must emit a closed-enum ``state``
    field disclosing the degraded-resolution branch."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-SSSS REAL BUG (state axis): --staged-on-clean path "
            "emits plain text -- no envelope, so no ``state`` field. "
            "Compound bug: must first emit a JSON envelope (Pattern-1-V-C) "
            "AND then populate ``summary.state`` (Pattern-1-V-D). Pinned "
            "strict; graduates when both conditions hold."
        ),
    )
    def test_staged_emits_state_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """--staged-on-clean must emit ``summary.state`` distinct from no-args."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "affected-tests")
        summary = data["summary"]
        state = summary.get("state")
        assert state and isinstance(state, str) and state.strip(), (
            f"W805-SSSS Pattern-1-V-D: --staged-on-clean must emit a "
            f"``summary.state`` field disclosing the degraded-resolution "
            f"branch (helper returned []); got {state!r}"
        )


class TestStagedResolutionDisclosure:
    """Mirror axis: a --staged invocation where the helper returns []
    must emit a closed-enum ``resolution`` field, since this IS a
    degraded-resolution path under the shared helper."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-SSSS REAL BUG (resolution axis): --staged-on-clean path "
            "emits no envelope so no ``resolution`` field either. "
            "Pattern-1-V-D contract requires AT LEAST ONE closed-enum "
            "disclosure (state OR resolution) on the degraded-resolution "
            "path. Pinned strict; graduates when the envelope is emitted "
            "AND distinguishes --staged-empty from no-args on either "
            "field."
        ),
    )
    def test_staged_emits_resolution_or_state(self, cli_runner, clean_indexed_project, monkeypatch):
        """--staged-on-clean must emit ``summary.resolution`` OR ``summary.state``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "affected-tests")
        summary = data["summary"]
        resolution = summary.get("resolution")
        state = summary.get("state")
        assert (resolution and isinstance(resolution, str) and resolution.strip()) or (
            state and isinstance(state, str) and state.strip()
        ), (
            f"W805-SSSS Pattern-1-V-D: --staged-on-clean path must emit "
            f"summary.resolution OR summary.state; "
            f"got resolution={resolution!r} state={state!r}"
        )


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_affected_tests inherits the same
    silent-SAFE shape as cmd_diff, cmd_pr_diff, cmd_attest, and
    cmd_test_gaps via the shared ``get_changed_files`` helper. FIFTH
    strict consumer -- elevates the family to 5-STRONG STRUCTURAL.
    Pins the inheritance so a fix to the shared helper unblocks ALL
    FIVE consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-SSSS FAMILY-CONFIRMATION: cmd_affected_tests's --staged-"
            "on-clean path emits no envelope so no ``git_error`` field "
            "-- the same gap W805-EEEE pins on cmd_diff, W805-JJJJ pins "
            "on cmd_pr_diff, W805-OOOO pins on cmd_attest, and W805-RRRR "
            "pins on cmd_test_gaps. The shared helper "
            "``src/roam/commands/changed_files.py:131-146`` returns an "
            "empty list on three distinct failure classes (returncode "
            "!= 0, FileNotFoundError, TimeoutExpired). All FIVE consumers "
            "(cmd_diff, cmd_pr_diff, cmd_attest, cmd_test_gaps, "
            "cmd_affected_tests) inherit silent-SAFE -- the family is "
            "now 5-STRONG STRUCTURAL. Pinned strict; graduates when "
            "``get_changed_files`` returns a ``(paths, error_kind)`` "
            "tuple and cmd_affected_tests surfaces ``summary.git_error`` "
            "on the failure branch."
        ),
    )
    def test_staged_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """--staged-on-clean must emit ``summary.git_error`` distinct from no-args."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "affected-tests")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-SSSS: --staged-on-clean path must emit summary.git_error distinct from no-args; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-checks -- W805-EEEE + W805-JJJJ + W805-OOOO +
# W805-RRRR invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper MUST NOT perturb the clean-tree
# no-changes envelopes the prior sister commands emit.
# ---------------------------------------------------------------------------


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
            f"W805-SSSS sister cross-check: cmd_test_gaps no-args must "
            f"still emit ``no changed files`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
        assert summary.get("total_gaps") == 0


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
        assert summary.get("state") == "no_changes", (
            f"W805-SSSS sister cross-check: cmd_attest clean-tree must "
            f"still emit state=no_changes; got {summary.get('state')!r}"
        )
        assert summary.get("safe_to_merge") is None, (
            f"W805-SSSS sister cross-check: cmd_attest clean-tree must "
            f"still emit safe_to_merge=null; "
            f"got {summary.get('safe_to_merge')!r}"
        )


# ---------------------------------------------------------------------------
# Positive regression -- the existing no-args + missing-target paths
# stay structurally stable. Guards against an over-correcting fix
# that lands the JSON-envelope upgrade without preserving the
# exit-code-1 "missing required input" contract.
# ---------------------------------------------------------------------------


class TestCleanAffectedTestsPositiveRegression:
    """Positive regression: no-args invocation (no target, no --staged)
    still exits 1 with a usage hint. This is the pre-W805-SSSS contract
    -- the user-error path must NOT be merged with the helper-failure
    path on the disambiguation fix."""

    def test_no_args_no_staged_exit_1_with_usage_hint(self, cli_runner, clean_indexed_project, monkeypatch):
        """No target, no --staged: exit 1 with usage hint."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected-tests"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 1, f"no-args path must exit 1 (usage error); got {result.exit_code}"
        out = getattr(result, "stdout", None) or result.output
        assert "target" in out.lower() or "staged" in out.lower(), (
            f"no-args path must mention ``TARGET`` or ``--staged`` in the usage hint; got {out!r}"
        )
