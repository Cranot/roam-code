"""W805-JJJJ -- shared-helper silent-SAFE probe on ``roam pr-diff``.

Eighty-eighth-in-batch W805 sweep. THIRD candidate for the
shared-helper resolution-disclosure family established by:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via
    ``get_changed_files`` returning ``[]`` on all failure classes.
  * W805-AAAA (cmd_delete_check) -- disclosed-but-unstructured
    ``git_error`` sentinel via an INDEPENDENT private ``_git_diff``
    helper (NOT shared with ``get_changed_files``).

Confirming a third member elevates the shared-helper pattern to a
structural class. W805-HHHH disconfirmed cmd_critique because it
consumes diff TEXT not git refs; the family remained 2-strong with
only ONE actual shared-helper consumer (cmd_diff).

W978 first-hypothesis: cmd_pr_diff consumes the shared helper
--------------------------------------------------------------

Audit of ``src/roam/commands/cmd_pr_diff.py`` head-to-tail:

  * Line 19: ``from roam.commands.changed_files import
    get_changed_files, resolve_changed_to_db``. The import IS the
    shared helper used by cmd_diff (W805-EEEE).
  * Line 78: ``changed = get_changed_files(root, staged=staged,
    commit_range=commit_range)``. Same call shape as
    ``cmd_diff.py:475`` (W805-EEEE). cmd_pr_diff is a CONFIRMED
    consumer of the same fallible boundary.
  * Lines 79-113: ``if not changed:`` branch emits
    ``verdict: "no changes detected"`` + ``footprint_pct: 0.0`` +
    ``metric_deltas_available: false`` -- the SAME envelope shape on
    both clean-tree and bogus-ref paths.

W978 finding: CONFIRMED shared-helper consumer. The first
hypothesis ("cmd_pr_diff inherits the W805-EEEE silent-SAFE shape")
is correct. cmd_pr_diff is the SECOND actual shared-helper consumer
in the family (after cmd_diff), making the SHARED-HELPER family
3-strong total (cmd_diff + cmd_pr_diff via the shared helper, plus
cmd_delete_check on an analogous-but-independent axis).

Probe results (/tmp/w805_jjjj_probe isolation run)
--------------------------------------------------

* ``roam --json pr-diff`` on clean tree:
  exit 0, ``verdict: "no changes detected"``,
  ``footprint_pct: 0.0``, ``metric_deltas_available: false``,
  ``partial_success: false``. NO ``state``, NO ``resolution``,
  NO ``git_error`` field.

* ``roam --json pr-diff --range nonexistent_branch..HEAD``:
  BYTE-IDENTICAL ``summary`` to the clean-tree envelope above.
  Same ``verdict``, same fields, same values. An agent has ZERO
  machine-state fields to switch on.

* ``roam --json pr-diff --staged`` on clean tree (no staged files):
  same shape.

STRICTLY MORE SEVERE than cmd_diff (W805-EEEE)
----------------------------------------------

cmd_diff at least emits ``state: "no_changes"`` -- a closed-enum
token that, while ambiguous between clean-tree and bogus-ref, IS at
least a machine-state field. cmd_pr_diff has NO ``state`` field at
all. The envelope distinguishes the two paths on ZERO machine-state
fields. This is the worst Pattern-1-V-D shape observed across the
W805 sweep.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_pr_diff.py for the W907 patterns (``avoid.*cycle``,
``avoid.*circular``, ``prevent.*import.*cycle``, ``defer.*import``):
NO matches. The lazy imports at lines 120-127 (``metrics_history`` /
``graph.diff``) and lines 197, 206, 271 (``exit_codes``) are
performance deferrals, not false-cycle hedges. Clean on W907.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff (shared-helper consumer, CATASTROPHIC).
  * W805-AAAA: cmd_delete_check (independent helper, disclosed).
  * W805-HHHH: cmd_critique DISCONFIRMED (consumes diff text).

After this probe:
  * W805-EEEE: cmd_diff (shared-helper consumer).
  * W805-JJJJ: cmd_pr_diff (shared-helper consumer) -- THIRD MEMBER
    via the shared-helper axis (CONFIRMED).
  * W805-AAAA: cmd_delete_check (independent helper).

SHARED-HELPER FAMILY: 3-strong total members (counting cmd_diff,
cmd_pr_diff, and cmd_delete_check), 2-strong on the strict
``get_changed_files`` axis. Pattern is now structural: any consumer
of ``get_changed_files`` inherits silent-SAFE on bogus-ref unless
the helper is upgraded to return ``(paths, error_kind)``.

Next W805 sweep candidate (W805-KKKK)
-------------------------------------

Per task spec: cmd_workspace. Workspace operations frequently
consume changed-files / git-ref helpers and may exhibit the same
inherited silent-SAFE shape. cmd_pr_risk (the W607-Q probe) is
in-flight and NOT a candidate this round per the hard constraints.
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

    Used as the baseline -- the W805-JJJJ bug is that a bogus-ref
    invocation should NOT be byte-identical to this clean-tree case.
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
# W978 verification -- cmd_pr_diff actually consumes get_changed_files.
# This test asserts the source-level contract that founds the W805-JJJJ
# probe. If cmd_pr_diff is refactored to a different helper, this test
# graduates the W805-JJJJ pin to "not applicable" rather than letting
# the bug class hide behind a stale assertion.
# ---------------------------------------------------------------------------


class TestCmdPrDiffConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_pr_diff is a confirmed
    consumer of ``get_changed_files`` from
    ``src/roam/commands/changed_files.py``. This is the source-level
    invariant that elevates W805-JJJJ from a coincidental shape match
    to a structural shared-helper class member."""

    def test_cmd_pr_diff_consumes_get_changed_files(self):
        """Source-level check: cmd_pr_diff imports + calls get_changed_files.

        Fails if a refactor moves cmd_pr_diff onto a different helper.
        At that point the W805-JJJJ pin is structurally stale and the
        new helper must be re-audited for the same bug class.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_pr_diff.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-JJJJ W978-precondition: cmd_pr_diff must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files" in src, (
            "W805-JJJJ W978-precondition: cmd_pr_diff must reference "
            "get_changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files(root" in src, (
            "W805-JJJJ W978-precondition: cmd_pr_diff must CALL "
            "get_changed_files(root, ...); if the call site moved, "
            "re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash on degenerate diff-source paths.
# Guard-rail: any future W805-JJJJ fix must not reintroduce the
# empty-stdout crash class while adding disclosure on top.
# ---------------------------------------------------------------------------


class TestPrDiffSourceNoCrash:
    """Bogus diff-source paths must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_bogus_commit_range_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range: non-empty stdout, parseable JSON, no exception."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff", "--range", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"bogus commit-range must exit 0; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus-range"
        data = parse_json_output(result, "pr-diff")
        assert isinstance(data, dict)

    def test_staged_on_clean_tree_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """``--staged`` on a clean tree: non-empty stdout, parseable JSON."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on --staged clean"
        data = parse_json_output(result, "pr-diff")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# REAL BUG pinned strict.
#
# cmd_pr_diff inherits silent-SAFE from get_changed_files. The envelope
# emits ``verdict: "no changes detected"`` + ``partial_success: false``
# on BOTH clean-tree AND bogus-ref paths. STRICTLY MORE SEVERE than
# cmd_diff (W805-EEEE) because cmd_pr_diff lacks even a ``state`` field.
# ---------------------------------------------------------------------------


class TestBogusCommitRangeStateDisclosure:
    """The bogus-commit-range path produces an envelope indistinguishable
    from a clean working tree. There is NO state field, NO resolution
    field, NO git_error field, NO partial_success: true. An agent acting
    on the verdict concludes the diff is safe."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJ REAL BUG: src/roam/commands/cmd_pr_diff.py:78-113 "
            "(the ``if not changed:`` branch downstream of "
            '``get_changed_files``) emits ``verdict: "no changes '
            'detected"`` + ``partial_success: false`` on a bogus '
            "commit-range -- byte-identical to the clean-tree envelope. "
            "The root cause is "
            "``src/roam/commands/changed_files.py:142,145`` swallowing "
            "``returncode != 0`` / FileNotFoundError / TimeoutExpired into "
            "an empty list. Pattern-1-V-D silent-success-on-degraded-"
            "resolution. STRICTLY MORE SEVERE than W805-EEEE (cmd_diff) "
            'which at least emits ``state: "no_changes"`` -- cmd_pr_diff '
            "has NO ``state`` field at all. THIRD shared-helper family "
            "member; SECOND on the strict ``get_changed_files`` axis. "
            "Pinned strict; graduates when the bogus-ref path emits "
            "``state`` with a non-``no_changes`` closed-enum value."
        ),
    )
    def test_bogus_commit_range_state_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range path must emit a non-``no_changes`` ``state``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff", "--range", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-diff")
        summary = data["summary"]
        state = summary.get("state")
        # The bug: state is None / "no_changes" -- byte-identical to clean tree.
        assert state and state != "no_changes", (
            f"W805-JJJJ Pattern-1-V-D: bogus-commit-range path must emit "
            f"a non-``no_changes`` summary.state to distinguish typo'd "
            f"ref from genuinely clean working tree; got {state!r}"
        )


class TestBogusCommitRangeResolutionDisclosure:
    """Mirror axis: a bogus positional ref must emit a closed-enum
    ``resolution`` field. cmd_pr_diff has no positional ref argument
    (uses ``--range``) so the resolution test rides on the same flag."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJ REAL BUG (resolution axis): bogus --range path "
            "emits no ``resolution`` field. Pattern-1-V-D contract "
            "requires AT LEAST ONE closed-enum disclosure (state OR "
            "resolution) on the degraded-resolution path. Pinned strict; "
            "graduates when the envelope distinguishes bogus-ref from "
            "clean-tree on either field."
        ),
    )
    def test_bogus_commit_range_resolution_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus --range must emit ``summary.resolution`` OR a non-empty state."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff", "--range", "totally_fake_ref..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-diff")
        summary = data["summary"]
        resolution = summary.get("resolution")
        state = summary.get("state")
        assert (resolution and isinstance(resolution, str) and resolution.strip()) or (
            state and state != "no_changes"
        ), (
            f"W805-JJJJ Pattern-1-V-D: bogus --range path must emit "
            f"summary.resolution OR a non-``no_changes`` summary.state; "
            f"got resolution={resolution!r} state={state!r}"
        )


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_pr_diff inherits the same silent-SAFE
    shape as cmd_diff via the shared ``get_changed_files`` helper. This
    is the structural class confirmation -- pins the inheritance so a
    fix to the shared helper unblocks BOTH consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJ FAMILY-CONFIRMATION: cmd_pr_diff's bogus-ref path "
            "emits no ``git_error`` field -- the same gap W805-EEEE pins "
            "on cmd_diff. The shared helper "
            "``src/roam/commands/changed_files.py:131-146`` returns an "
            "empty list on three distinct failure classes (returncode != "
            "0, FileNotFoundError, TimeoutExpired). Both consumers "
            "(cmd_diff and cmd_pr_diff) inherit silent-SAFE. THIRD "
            "shared-helper family member confirmed. Pinned strict; "
            "graduates when ``get_changed_files`` returns a "
            "``(paths, error_kind)`` tuple and cmd_pr_diff surfaces "
            "``summary.git_error`` on the failure branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref path must emit ``summary.git_error`` distinct from clean tree."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["pr-diff", "--range", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "pr-diff")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-JJJJ: bogus-ref path must emit summary.git_error "
            f"distinct from clean tree (which has no git failure); "
            f"got {git_error!r}"
        )


class TestEmptyPrDistinctFromUnresolvedRef:
    """Pattern-2 invariant: a genuinely-clean working tree and a
    git-error path MUST produce distinguishable envelopes. Today they
    are byte-identical on every machine-state field cmd_pr_diff emits."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJ REAL BUG (invariant): clean-tree envelope and "
            "bogus-ref envelope are byte-identical on every machine-state "
            "field (state, resolution, git_error, partial_success). "
            "Pattern-2 silent-fallback contract violated. Pinned strict; "
            "graduates when the two envelopes differ on at least one "
            "closed-enum machine-state field."
        ),
    )
    def test_clean_tree_distinct_from_bogus_ref(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean-tree envelope must differ from bogus-ref envelope on a machine-state field."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["pr-diff"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_result = invoke_cli(
            cli_runner,
            ["pr-diff", "--range", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_summary = parse_json_output(clean_result, "pr-diff")["summary"]
        bogus_summary = parse_json_output(bogus_result, "pr-diff")["summary"]

        machine_state_fields = ("state", "resolution", "git_error", "partial_success")
        differing = [f for f in machine_state_fields if clean_summary.get(f) != bogus_summary.get(f)]
        assert differing, (
            f"W805-JJJJ Pattern-2: clean-tree and bogus-ref envelopes "
            f"must differ on at least one machine-state field "
            f"({machine_state_fields}); got identical values "
            f"clean={ {f: clean_summary.get(f) for f in machine_state_fields} } "
            f"bogus={ {f: bogus_summary.get(f) for f in machine_state_fields} }"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-checks -- W805-EEEE + W805-AAAA invariants must
# stay green. A future fix to the shared ``get_changed_files`` helper
# (which would graduate W805-EEEE and W805-JJJJ atomically) MUST NOT
# perturb the W805-AAAA cmd_delete_check shape.
# ---------------------------------------------------------------------------


class TestW805EeeeInvariantsPreserved:
    """Sister cross-check: cmd_diff's W805-EEEE no-changes envelope shape
    is preserved. The clean-tree branch still emits state=no_changes."""

    def test_diff_clean_tree_state_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_diff clean tree still emits state=no_changes (W641-followup-E)."""
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
        # The pre-W805-EEEE contract: clean tree IS state=no_changes.
        # Must stay after any future shared-helper fix.
        assert summary.get("state") == "no_changes", (
            f"W805-JJJJ sister cross-check: cmd_diff clean-tree must "
            f"still emit state=no_changes; got {summary.get('state')!r}"
        )
        assert summary.get("verdict") == "no changes"
        assert summary.get("partial_success") is False


class TestW805AaaaInvariantsPreserved:
    """Sister cross-check: cmd_delete_check's W805-AAAA git_error shape
    is preserved. The independent ``_git_diff`` helper still emits
    git_error=git_error on bogus ref."""

    def test_delete_check_git_error_field_still_emitted(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_delete_check still emits ``summary.git_error`` on bogus ref."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["delete-check", "--commit-range", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "delete-check")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error == "git_error", (
            f"W805-JJJJ sister cross-check: cmd_delete_check's existing "
            f"summary.git_error contract must stay; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Positive regression -- clean diff sources still produce real verdicts.
# Guards against an over-correcting fix-forward.
# ---------------------------------------------------------------------------


class TestCleanPrDiffPositiveRegression:
    """Positive regression: a real populated diff (no bogus ref) still
    produces a real verdict with the existing envelope shape."""

    def test_clean_tree_still_emits_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """Positive regression: clean tree still emits the canonical
        no-changes envelope. This is the pre-W805-JJJJ contract -- it
        must stay even after the bogus-ref path is disambiguated."""
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
        # cmd_pr_diff's clean-tree verdict is "no changes detected"
        # (distinct from cmd_diff's "no changes").
        assert "no change" in summary.get("verdict", "").lower(), (
            f"Positive regression: clean-tree verdict must mention ``no change``; got {summary.get('verdict')!r}"
        )
        assert summary.get("partial_success") is False
        assert summary.get("footprint_pct") == 0.0
