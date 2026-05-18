"""W805-AAAAA -- shared-helper silent-SAFE probe on ``roam boundary``.

Hundred-and-fifth-in-batch W805 sweep. EIGHTH potential strict consumer
for the shared-helper resolution-disclosure family on the
``get_changed_files`` axis.

Family lineage entering this probe:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO (cmd_attest) -- THIRD strict consumer.
  * W805-RRRR (cmd_test_gaps) -- FOURTH strict consumer.
  * W805-SSSS (cmd_affected_tests) -- FIFTH (STRICTLY WORST -- plain text
    in --json mode).
  * W805-VVVV (cmd_affected) -- SIXTH (envelope shape, two call sites).
  * W805-XXXX (cmd_adversarial) -- SEVENTH (envelope shape).
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper on
    the analogous-but-not-shared axis.

Family stood 7-STRONG STRUCTURAL on the strict ``get_changed_files`` axis
at the start of this probe. cmd_boundary is the W805-XXXX agent's
recommended 8th probe per canonical ordering.

W978 first-hypothesis: cmd_boundary is a strict shared-helper consumer
----------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_boundary.py`` head-to-tail:

  * Line 52: ``from roam.commands.changed_files import get_changed_files``.
    The import IS the shared helper used by all seven prior strict
    consumers.
  * Lines 489-497: FOUR call sites, one per ``--changed-range`` value:
    ``pr`` (with ``base_ref``), ``staged``, ``head``, and the default
    (working-tree). All four paths feed ``changed_files``. Helper
    returncode != 0 silently returns []. The ``--changed-range pr
    --base-ref totally-bogus-99`` invocation flows through the helper
    with the bogus base ref; the helper swallows the failure.
  * Line 504: ``wrong_findings = _scan_wrong_direction_imports(conn,
    changed_files)`` -- this scanner is scoped to changed_files (an
    empty set yields no findings).
  * Line 551 vs 553: the verdict for ``total == 0`` is
    ``"0 boundary findings (scope: {cr})"`` where ``cr`` is the
    --changed-range CLI flag value. Bogus-ref-on-pr and clean-tree-on-pr
    BOTH emit ``"0 boundary findings (scope: pr)"`` -- IDENTICAL verdicts
    despite distinct failure classes.
  * No ``state`` / ``git_error`` / ``resolution`` field on the
    bogus-ref envelope branch. (The ``state: "no_imports"`` field only
    fires when the corpus has 0 import edges, i.e. uninitialized index --
    that is a DIFFERENT failure class and does NOT cover bogus-ref.)
  * Line 541: ``partial_success = (n_wrong == 0 and total > 0 and cr !=
    "all") or empty_corpus``. With ``total == 0`` and a populated index,
    ``partial_success`` is False on BOTH paths -- silent-SAFE on the
    partial_success axis too.

W978 finding: CONFIRMED + W805-EEEE/RRRR/VVVV/XXXX-SHAPE-MATCH.
cmd_boundary inherits the same envelope-present-but-state-opaque
silent-SAFE shape via the shared helper. This is the EIGHTH strict
consumer; the shared-helper family on the ``get_changed_files`` axis
elevates from 7-STRONG to 8-STRONG STRUCTURAL.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_boundary.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): only the deferred SARIF import at
line 571 (``from roam.output.sarif import write_sarif`` inside the
``--sarif`` branch). That deferral is a legit lazy-import to avoid
loading the SARIF helper on the hot path, NOT a false cycle hedge --
the import is bare and the surrounding ``try/except ImportError`` block
discloses a graceful-degradation path rather than hedging a nonexistent
cycle. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 7-STRONG STRUCTURAL (EEEE / JJJJ / OOOO / RRRR /
SSSS / VVVV / XXXX) + 1 independent (AAAA).

After this probe: 8-STRONG STRUCTURAL (add cmd_boundary) + 1 independent
= 9 family members total.

W805 sweep update
-----------------

W805 sweep yield 53/53 (this probe = 53rd). Strict-consumer family is
now 8-STRONG fully structural. A single fix to ``get_changed_files``
(returning ``(paths, error_kind)``) atomically unblocks EIGHT consumers.

Next W805 sweep candidate (W805-BBBBB)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-XXXX
canonical-list ordering: cmd_why_slow, cmd_verify, cmd_syntax_check,
cmd_suggest_reviewers, cmd_coupling, cmd_plan. W805-BBBBB candidate:
cmd_why_slow -- next-most-likely shared-helper consumer per the
W805-XXXX strict-consumer ordering.
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
    """Indexed project with a clean working tree and NO public_by_accident.

    cmd_boundary's ``public_by_accident`` scan runs on the FULL corpus
    regardless of ``--changed-range``, so the fixture must NOT contain any
    underscore-prefixed names in ``__all__`` -- otherwise the kind A
    findings mask the silent-SAFE behavior on the kind B (changed-range)
    axis. The fixture is deliberately minimal: two functions, no
    ``__all__`` declaration, clean import graph.

    The W805-AAAAA bug is that an invocation against this clean tree
    (helper returns []) is INDISTINGUISHABLE from an invocation against
    a bogus base ref (helper ALSO returns [] but for a different failure
    class). Both paths emit the same ``"0 boundary findings (scope: ...)"``
    verdict with no closed-enum state / git_error disclosure.
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
# W978 verification -- cmd_boundary actually consumes get_changed_files.
# If cmd_boundary is refactored to a different helper or merges its call
# sites away, this test surfaces the structural drift before the
# W805-AAAAA pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdBoundaryConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_boundary is a confirmed
    consumer of ``get_changed_files``. Source-level invariant elevating
    W805-AAAAA from a coincidental shape match to a structural shared-
    helper class member."""

    def test_cmd_boundary_consumes_get_changed_files(self):
        """Source-level check: cmd_boundary imports + calls get_changed_files."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_boundary.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-AAAAA W978-precondition: cmd_boundary must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files(" in src, (
            "W805-AAAAA W978-precondition: cmd_boundary must call "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )


# ---------------------------------------------------------------------------
# Sanity / W978 second-hypothesis -- cmd_boundary --json on a clean tree
# emits a parseable JSON envelope (NOT plain text). Distinguishes the
# shape from W805-SSSS cmd_affected_tests strictly-worst shape.
# ---------------------------------------------------------------------------


class TestJsonEnvelopeInJsonMode:
    """W978 verification: cmd_boundary --json on a clean tree emits a
    parseable JSON envelope (NOT plain text). Distinguishes the shape
    from W805-SSSS cmd_affected_tests strictly-worst shape."""

    def test_clean_tree_emits_parseable_json_envelope(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean tree must emit a parseable JSON envelope (not plain text)."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "main"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"clean tree must exit 0; got {result.exit_code}; stderr={result.output!r}"
        data = parse_json_output(result, "boundary")
        assert isinstance(data, dict), (
            f"W805-AAAAA W978: cmd_boundary --json on clean tree must "
            f"emit a parseable JSON envelope dict (NOT the W805-SSSS "
            f"plain-text strictly-worst shape); got {type(data).__name__}"
        )
        assert data.get("command") == "boundary"


# ---------------------------------------------------------------------------
# Pattern-1 Variant D -- bogus-ref input MUST be distinguishable from
# clean-tree empty-diff. REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestBogusRefDistinctFromEmptyDiff:
    """The bogus-ref path (helper returncode != 0) must be distinguishable
    from the clean-tree path (helper returns [] legitimately). Today both
    emit the IDENTICAL ``"0 boundary findings (scope: pr)"`` verdict --
    Pattern-1 Variant D silent-fallback on a degraded resolution."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAAA REAL BUG: src/roam/commands/cmd_boundary.py:491 "
            "(``get_changed_files(project_root, pr=True, base_ref=base_ref)``) "
            "inherits silent-SAFE from the shared helper at "
            "src/roam/commands/changed_files.py:131-146. When "
            "``--base-ref totally-bogus-99`` is passed, the helper "
            "swallows the returncode != 0 and returns []; cmd_boundary's "
            "``total == 0`` branch (lines 550-551) emits the same "
            "``'0 boundary findings (scope: pr)'`` verdict as a clean "
            "tree run against the real base ref. Pattern-1 Variant D: "
            "degraded-resolution paths must be distinguishable from "
            "full-resolution-with-no-changes paths. EIGHTH strict shared-"
            "helper consumer; family is now 8-STRONG STRUCTURAL on the "
            "get_changed_files axis. Pinned strict; graduates when the "
            "bogus-ref verdict differs from the clean-tree verdict."
        ),
    )
    def test_bogus_ref_verdict_differs_from_clean_tree(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref verdict must differ from clean-tree verdict."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "main"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_data = parse_json_output(clean_result, "boundary")
        bogus_result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_data = parse_json_output(bogus_result, "boundary")
        clean_verdict = clean_data["summary"].get("verdict")
        bogus_verdict = bogus_data["summary"].get("verdict")
        assert clean_verdict != bogus_verdict, (
            f"W805-AAAAA Pattern-1-V-D: bogus-ref verdict must differ from "
            f"clean-tree verdict; both got {clean_verdict!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1-V-D state disclosure -- the ``total == 0`` branch on the
# bogus-ref path must emit a closed-enum ``state`` / ``git_error`` /
# ``resolution`` field disclosing WHICH failure class drove the helper
# to return []. REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The bogus-ref ``total == 0`` branch must emit a closed-enum
    ``state`` field disclosing the degraded-resolution branch (helper
    returncode != 0 vs legitimately-empty diff). cmd_boundary already
    has a ``state: "no_imports"`` literal for the empty-corpus path
    (line 593) -- the bogus-ref path needs the same disclosure
    discipline."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAAA REAL BUG (state axis): cmd_boundary's "
            "``total == 0`` branch (lines 550-551, 582-589) emits a JSON "
            "envelope WITHOUT a ``summary.state`` field on the bogus-ref "
            "path. cmd_boundary DOES emit ``state: 'no_imports'`` when "
            "the corpus has 0 import edges (line 593) -- proving the "
            "command knows how to disclose closed-enum state -- but the "
            "bogus-ref failure class is silently merged into the "
            "no-findings success path. The branch covers three distinct "
            "conditions: (1) legitimately-empty diff with imports, (2) "
            "helper returncode != 0 (bogus ref / git not found), (3) "
            "FileNotFoundError / TimeoutExpired in the helper subprocess "
            "call. Pattern-1-V-D requires a closed-enum disclosure "
            "(state OR git_error OR resolution) on the degraded-"
            "resolution branch. Pinned strict; graduates when "
            "cmd_boundary populates ``summary.state`` distinct between "
            "the no-changes and helper-failure classes."
        ),
    )
    def test_bogus_ref_emits_state_or_git_error(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.state`` or ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "boundary")
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        # The "no_imports" state IS already used by cmd_boundary for the
        # empty-corpus case -- exclude it from this assertion so the
        # bogus-ref case is forced to disclose a DIFFERENT closed-enum
        # value (e.g. "git_error" / "bad_ref" / "diff_failed").
        bogus_state_ok = state and isinstance(state, str) and state.strip() and state != "no_imports"
        assert (
            bogus_state_ok
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-AAAAA Pattern-1-V-D: bogus-ref path must emit a closed-"
            f"enum disclosure (summary.state != 'no_imports' OR "
            f"summary.git_error OR summary.resolution); got state={state!r} "
            f"git_error={git_error!r} resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Family-confirmation -- cmd_boundary inherits silent-SAFE via the
# shared helper, the EIGHTH strict consumer of the family. Pins the
# inheritance so a fix to the shared helper unblocks ALL EIGHT consumers
# atomically.
# ---------------------------------------------------------------------------


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_boundary inherits the same silent-
    SAFE shape as cmd_diff, cmd_pr_diff, cmd_attest, cmd_test_gaps,
    cmd_affected_tests, cmd_affected, and cmd_adversarial via the shared
    ``get_changed_files`` helper. EIGHTH strict consumer -- elevates the
    family to 8-STRONG STRUCTURAL. Pins the inheritance so a fix to the
    shared helper unblocks ALL EIGHT consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-AAAAA FAMILY-CONFIRMATION: cmd_boundary's bogus-ref "
            "``total == 0`` branch emits a JSON envelope without any "
            "``git_error`` field -- the same gap W805-EEEE pins on cmd_diff, "
            "W805-JJJJ pins on cmd_pr_diff, W805-OOOO pins on cmd_attest, "
            "W805-RRRR pins on cmd_test_gaps, W805-SSSS pins on "
            "cmd_affected_tests, W805-VVVV pins on cmd_affected, and "
            "W805-XXXX pins on cmd_adversarial. The shared helper "
            "``src/roam/commands/changed_files.py:131-146`` returns an "
            "empty list on three distinct failure classes (returncode != "
            "0, FileNotFoundError, TimeoutExpired). All EIGHT consumers "
            "inherit silent-SAFE -- the family is now 8-STRONG "
            "STRUCTURAL. Pinned strict; graduates when "
            "``get_changed_files`` returns a ``(paths, error_kind)`` tuple "
            "and cmd_boundary surfaces ``summary.git_error`` on the "
            "failure branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "boundary")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-AAAAA: bogus-ref path must emit summary.git_error; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-XXXX invariants must stay green. A
# future fix to the shared ``get_changed_files`` helper MUST NOT perturb
# the clean-tree no-changes envelope cmd_adversarial emits.
# ---------------------------------------------------------------------------


class TestW805XxxxInvariantsPreserved:
    """Sister cross-check: cmd_adversarial's W805-XXXX clean-tree envelope
    shape is preserved. The clean-tree path still emits the canonical
    ``"No changes detected"`` verdict with a parseable JSON envelope."""

    def test_adversarial_clean_tree_still_emits_no_changes_verdict(
        self, cli_runner, clean_indexed_project, monkeypatch
    ):
        """cmd_adversarial clean-tree still emits ``No changes detected`` verdict."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["adversarial"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "adversarial")
        summary = data["summary"]
        assert "no changes" in summary.get("verdict", "").lower(), (
            f"W805-AAAAA sister cross-check: cmd_adversarial clean-tree "
            f"must still emit ``No changes`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
