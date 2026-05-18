"""W805-XXXX -- shared-helper silent-SAFE probe on ``roam adversarial``.

Hundred-and-second-in-batch W805 sweep. SEVENTH potential strict consumer
for the shared-helper resolution-disclosure family on the
``get_changed_files`` axis.

Family lineage entering this probe:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO (cmd_attest) -- THIRD strict consumer.
  * W805-RRRR (cmd_test_gaps) -- FOURTH strict consumer (active-misleading
    ``partial_success: False`` on the bogus-ref branch).
  * W805-SSSS (cmd_affected_tests) -- FIFTH (STRICTLY WORST -- plain text
    in --json mode).
  * W805-VVVV (cmd_affected) -- SIXTH (envelope shape, two call sites).
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper on
    the analogous-but-not-shared axis.

Family stood 6-strong on the strict ``get_changed_files`` axis at the
start of this probe. cmd_adversarial -- the architectural-challenge
sibling -- is the natural seventh inheritor per the W805-VVVV
canonical-list ordering.

W978 first-hypothesis: cmd_adversarial is a strict shared-helper consumer
-------------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_adversarial.py`` head-to-tail:

  * Line 21: ``from roam.commands.changed_files import get_changed_files,
    resolve_changed_to_db``. The import IS the shared helper used by all
    six prior strict consumers.
  * ``_run_check_ek("resolve_changed_files", get_changed_files, root, ...)``.
    Single shared-helper call site. Both bogus-ref
    (``--range totally-bogus..HEAD``) and clean-tree inputs flow through
    here -- helper returncode != 0 is silently swallowed and returns [].
  * Lines 726-752: ``if not changed:`` branch emits a JSON envelope in
    --json mode with ``summary.verdict = "No changes detected"``. No
    ``state`` / ``git_error`` / ``resolution`` field disclosing the
    helper-failure class. Matches W805-EEEE / W805-RRRR / W805-VVVV
    shape, NOT the W805-SSSS strictly-worst plain-text shape.
  * Lines 756-784: ``if not file_map:`` branch is a SECOND failure
    boundary -- changed files not found in index. Has its own verdict
    ("Changed files not found in index") and an explicit count. This
    branch IS distinct from the no-changes branch, so the "file
    resolution" axis is partially disclosed; the missing axis is
    "git helper returned [] -- WHY".

W978 finding: CONFIRMED + W805-EEEE/RRRR/VVVV-SHAPE-MATCH. cmd_adversarial
inherits the same envelope-present-but-state-opaque silent-SAFE shape
via the shared helper. This is the SEVENTH strict consumer; the shared-
helper family on the ``get_changed_files`` axis elevates from 6-STRONG
to 7-STRONG STRUCTURAL.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_adversarial.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): NO matches. cmd_adversarial uses
function-scope imports inside each ``_check_*`` helper but the comments
on those imports are bare ``try / except ImportError`` blocks that
disclose missing-graph-module degradation rather than hedge a
nonexistent cycle. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 6-STRONG (EEEE / JJJJ / OOOO / RRRR / SSSS / VVVV)
+ 1 independent (AAAA).

After this probe: 7-STRONG STRUCTURAL (add cmd_adversarial) + 1
independent = 8 family members total.

W805 sweep update
-----------------

W805 sweep yield 52/52 (this probe = 52nd). Strict-consumer family is
now 7-STRONG fully structural. A single fix to ``get_changed_files``
(returning ``(paths, error_kind)``) atomically unblocks SEVEN
consumers.

Next W805 sweep candidate (W805-YYYY)
-------------------------------------

Remaining unprobed strict-consumer candidates per the W805-VVVV
canonical-list ordering: cmd_boundary, cmd_why_slow, cmd_verify,
cmd_syntax_check, cmd_suggest_reviewers, cmd_coupling, cmd_plan.
W805-YYYY candidate: cmd_boundary -- next-most-likely shared-helper
consumer per the W805-VVVV strict-consumer ordering.
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

    The W805-XXXX bug is that an invocation against this clean tree
    (helper returns []) is INDISTINGUISHABLE from an invocation against
    a bogus ref (helper ALSO returns [] but for a different failure
    class). Both paths emit the same ``"No changes detected"`` verdict
    with no closed-enum state / git_error disclosure.
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
# W978 verification -- cmd_adversarial actually consumes get_changed_files.
# If cmd_adversarial is refactored to a different helper or merges its
# call site away, this test surfaces the structural drift before the
# W805-XXXX pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdAdversarialConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_adversarial is a confirmed
    consumer of ``get_changed_files``. Source-level invariant elevating
    W805-XXXX from a coincidental shape match to a structural shared-
    helper class member."""

    def test_cmd_adversarial_consumes_get_changed_files(self):
        """Source-level check: cmd_adversarial imports + calls get_changed_files."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_adversarial.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-XXXX W978-precondition: cmd_adversarial must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files," in src and "_run_check_ek(" in src, (
            "W805-XXXX W978-precondition: cmd_adversarial must call "
            "get_changed_files through the W607-EK wrapper; if this changed, "
            "re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Sanity / W978 second-hypothesis -- cmd_adversarial DOES emit a JSON
# envelope in --json mode on the no-changes branch (NOT the W805-SSSS
# plain-text-strictly-worst shape).
# ---------------------------------------------------------------------------


class TestJsonEnvelopeInJsonMode:
    """W978 verification: cmd_adversarial --json on a clean tree emits a
    parseable JSON envelope (NOT plain text). Distinguishes the shape
    from W805-SSSS cmd_affected_tests strictly-worst shape."""

    def test_clean_tree_emits_parseable_json_envelope(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean tree must emit a parseable JSON envelope (not plain text)."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["adversarial"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"clean tree must exit 0; got {result.exit_code}"
        data = parse_json_output(result, "adversarial")
        assert isinstance(data, dict), (
            f"W805-XXXX W978: cmd_adversarial --json on clean tree must "
            f"emit a parseable JSON envelope dict (NOT the W805-SSSS "
            f"plain-text strictly-worst shape); got {type(data).__name__}"
        )
        assert data.get("command") == "adversarial"


# ---------------------------------------------------------------------------
# Pattern-1 Variant D -- bogus-ref input MUST be distinguishable from
# clean-tree empty-diff. REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestBogusRefDistinctFromEmptyDiff:
    """The bogus-ref path (helper returncode != 0) must be distinguishable
    from the clean-tree path (helper returns [] legitimately). Today
    both emit the IDENTICAL ``"No changes detected"`` verdict --
    Pattern-1 Variant D silent-fallback on a degraded resolution."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-XXXX REAL BUG: cmd_adversarial's W607-EK "
            "resolve_changed_files wrapper still consumes get_changed_files, "
            "which inherits silent-SAFE from the shared helper at "
            "src/roam/commands/changed_files.py. When "
            "``--range totally-bogus..HEAD`` is passed, the helper "
            "swallows the returncode != 0 and returns []; cmd_adversarial's "
            "``if not changed:`` branch emits the same "
            "``'No changes detected'`` verdict as a legitimately-clean "
            "tree. Pattern-1 Variant D: degraded-resolution paths must "
            "be distinguishable from full-resolution-with-no-changes "
            "paths. SEVENTH strict shared-helper consumer; family is now "
            "7-STRONG STRUCTURAL on the get_changed_files axis. Pinned "
            "strict; graduates when the bogus-ref verdict differs from "
            "the clean-tree verdict."
        ),
    )
    def test_bogus_ref_verdict_differs_from_clean_tree(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref verdict must differ from clean-tree verdict."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["adversarial"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_data = parse_json_output(clean_result, "adversarial")
        bogus_result = invoke_cli(
            cli_runner,
            ["adversarial", "--range", "totally-bogus-ref-99..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_data = parse_json_output(bogus_result, "adversarial")
        clean_verdict = clean_data["summary"].get("verdict")
        bogus_verdict = bogus_data["summary"].get("verdict")
        assert clean_verdict != bogus_verdict, (
            f"W805-XXXX Pattern-1-V-D: bogus-ref verdict must differ from "
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
            "W805-XXXX REAL BUG (state axis): cmd_adversarial's "
            "``if not changed:`` branch (lines 726-752) emits a JSON "
            "envelope WITHOUT a ``summary.state`` field. The branch "
            "merges three distinct conditions: (1) legitimately-empty "
            "diff, (2) helper returncode != 0 (bogus ref / git not "
            "found), (3) FileNotFoundError / TimeoutExpired in the "
            "helper subprocess call. Pattern-1-V-D requires a closed-"
            "enum disclosure (state OR git_error OR resolution) on the "
            "degraded-resolution branch. Pinned strict; graduates when "
            "cmd_adversarial populates ``summary.state`` distinct between "
            "the no-changes and helper-failure classes."
        ),
    )
    def test_bogus_ref_emits_state_or_git_error(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.state`` or ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["adversarial", "--range", "totally-bogus-ref-99..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "adversarial")
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-XXXX Pattern-1-V-D: bogus-ref path must emit a closed-"
            f"enum disclosure (summary.state OR summary.git_error OR "
            f"summary.resolution); got state={state!r} "
            f"git_error={git_error!r} resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Family-confirmation -- cmd_adversarial inherits silent-SAFE via the
# shared helper, the SEVENTH strict consumer of the family. Pins the
# inheritance so a fix to the shared helper unblocks ALL SEVEN consumers
# atomically.
# ---------------------------------------------------------------------------


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_adversarial inherits the same silent-
    SAFE shape as cmd_diff, cmd_pr_diff, cmd_attest, cmd_test_gaps,
    cmd_affected_tests, and cmd_affected via the shared
    ``get_changed_files`` helper. SEVENTH strict consumer -- elevates the
    family to 7-STRONG STRUCTURAL. Pins the inheritance so a fix to the
    shared helper unblocks ALL SEVEN consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-XXXX FAMILY-CONFIRMATION: cmd_adversarial's bogus-ref "
            "``if not changed:`` branch emits a JSON envelope without any "
            "``git_error`` field -- the same gap W805-EEEE pins on cmd_diff, "
            "W805-JJJJ pins on cmd_pr_diff, W805-OOOO pins on cmd_attest, "
            "W805-RRRR pins on cmd_test_gaps, W805-SSSS pins on "
            "cmd_affected_tests, and W805-VVVV pins on cmd_affected. The "
            "shared helper ``src/roam/commands/changed_files.py:131-146`` "
            "returns an empty list on three distinct failure classes "
            "(returncode != 0, FileNotFoundError, TimeoutExpired). All "
            "SEVEN consumers inherit silent-SAFE -- the family is now "
            "7-STRONG STRUCTURAL. Pinned strict; graduates when "
            "``get_changed_files`` returns a ``(paths, error_kind)`` tuple "
            "and cmd_adversarial surfaces ``summary.git_error`` on the "
            "failure branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["adversarial", "--range", "totally-bogus-ref-99..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "adversarial")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-XXXX: bogus-ref path must emit summary.git_error; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-VVVV invariants must stay green. A
# future fix to the shared ``get_changed_files`` helper MUST NOT perturb
# the clean-tree no-changes envelope cmd_affected emits.
# ---------------------------------------------------------------------------


class TestW805VvvvInvariantsPreserved:
    """Sister cross-check: cmd_affected's W805-VVVV clean-tree envelope
    shape is preserved. The clean-tree path still emits the canonical
    ``"No changes detected"`` verdict with a parseable JSON envelope."""

    def test_affected_clean_tree_still_emits_no_changes_verdict(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_affected clean-tree still emits ``No changes detected`` verdict."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["affected"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "affected")
        summary = data["summary"]
        assert "no changes" in summary.get("verdict", "").lower(), (
            f"W805-XXXX sister cross-check: cmd_affected clean-tree must "
            f"still emit ``No changes`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
