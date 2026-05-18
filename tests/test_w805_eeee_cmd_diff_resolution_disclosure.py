"""W805-EEEE -- diff-source-axis Pattern-1-V-D + Pattern-2 probe on
``roam diff``.

Eighty-third-in-batch W805 sweep. Sister surface to cmd_delete_check
(W805-AAAA). The two surfaces share the same conceptual axis -- gating
on a git diff that may resolve through a bogus ref / source flag --
but use INDEPENDENT git-invocation helpers. The bug class manifests in
a STRICTLY MORE SEVERE shape on cmd_diff than on cmd_delete_check.

W978 first-hypothesis (sister-surface helper sharing)
-----------------------------------------------------

Before writing this file, audited both helpers head-to-tail:

  * ``cmd_delete_check.py:109-137`` -- ``_git_diff(root, source,
    base_ref, commit_range)`` returns ``(text, error_kind)`` where
    ``error_kind`` is one of {None, _GIT_MISSING, _GIT_TIMEOUT,
    _GIT_ERROR}. The CONSUMER at lines 296-343 surfaces the
    unavailability via ``git_error`` + ``partial_success: true`` +
    ``warnings_out``. State / resolution closed-enum is MISSING --
    W805-AAAA pins this gap.

  * ``cmd_diff.py:475`` calls ``get_changed_files(root, staged=staged,
    commit_range=commit_range)`` from
    ``src/roam/commands/changed_files.py:98-168``. That helper
    SWALLOWS ALL git errors (``returncode != 0`` returns ``[]``;
    ``FileNotFoundError`` / ``TimeoutExpired`` returns ``[]``) and
    returns an empty list INDISTINGUISHABLE from a clean tree.

The helpers are INDEPENDENT. cmd_diff inherits a STRICTLY WORSE
shape: where cmd_delete_check at least emits a ``git_error`` sentinel
+ ``partial_success: true``, cmd_diff emits
``verdict: "no changes"`` / ``state: "no_changes"`` /
``partial_success: false`` -- the same envelope it produces on a
genuinely clean working tree. This is the canonical Pattern-1-V-D
"silent success on degraded resolution": a user typo on a ref turns
into the strongest possible safety signal.

Probe results (this commit, /tmp/w805_eeee_probe isolation run)
---------------------------------------------------------------

* ``roam --json diff nonexistent_branch..HEAD``:
  exit 0, ``verdict: "no changes"``, ``state: "no_changes"``,
  ``partial_success: false``, ``message: "No changes found for
  nonexistent_branch..HEAD."``. NO ``git_error`` field. NO
  ``resolution`` field. NO ``warnings_out`` array. An agent
  consuming the envelope CANNOT distinguish a typo'd ref from a
  genuinely clean working tree.

* ``roam --json diff totally_fake_ref``: identical shape.
  ``label: "totally_fake_ref"`` is the ONLY field that differs from
  the genuinely-clean-tree envelope, and it's a free-form string,
  not a closed-enum machine-state token.

* ``roam --json diff`` on clean tree: same envelope shape
  (``verdict: "no changes"``, ``state: "no_changes"``,
  ``partial_success: false``, ``label: "unstaged"``).

CRITICAL agent-safety class
---------------------------

This is the W805-AAAA bug class GRADED UP. cmd_delete_check at least
discloses ``git_error: "git_error"`` + ``partial_success: true`` --
an agent can detect "something failed" without text-matching the
verdict. cmd_diff DOES NOT. The Pattern-1-V-D contract requires the
silent-fallback-on-degraded-resolution path to be loudly
distinguishable from the success path; cmd_diff makes the two
envelopes byte-identical except for the ``label`` field.

The resolution-disclosure family
--------------------------------

W805-AAAA + W805-EEEE together establish a TWO-MEMBER family of
diff-source-axis Pattern-1-V-D resolution-disclosure gaps. The two
members differ in SEVERITY (cmd_diff is worse) but share the same
SHAPE: a bogus ref / source flag silently passes through the
``get_changed_files`` / ``_git_diff`` boundary as "no changes" /
``git_error`` without a closed-enum state/resolution disclosure.

Future W805 sweep candidates that consume the same helpers:
``cmd_pr_diff`` (separate command -- distinct from cmd_diff;
uses a DIFFERENT base/target comparison axis), ``cmd_critique``
(consumes ``get_changed_files`` indirectly via ``cmd_diff``),
``cmd_pr_risk`` (same chain).

W978 + W907 compliance
----------------------

* W978: probed in isolation before writing. Confirmed independent
  helper shape (cmd_delete_check uses ``_git_diff`` private helper;
  cmd_diff uses shared ``get_changed_files``). The bug class IS
  present on cmd_diff but the manifestation differs from
  cmd_delete_check -- cmd_diff has a STRICTLY WORSE disclosure
  shape (silent SAFE vs disclosed-but-unstructured failure).
* W907: no false-cycle docstrings in cmd_diff.py. Lazy imports
  for ``networkx`` / ``batched_in`` / ``_gather_affected_tests`` /
  ``_load_rules`` are legitimate heavy-import deferrals, not false
  cycle hedges. No grep hits for ``avoid.*cycle`` /
  ``avoid.*circular`` / ``prevent.*import.*cycle`` /
  ``defer.*import`` in cmd_diff.py.

W805 sweep
----------

Per task spec this is the eighty-third-in-batch W805 sweep. Sister
surface to W805-AAAA (cmd_delete_check). Together they establish the
resolution-disclosure family on the diff-source axis. Next candidate
(W805-FFFF): probe ``cmd_critique`` -- the third consumer of
``get_changed_files`` (via ``cmd_diff``). cmd_critique gates on the
same diff and may inherit the same silent-SAFE shape transitively.
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

    Used as the baseline -- the W805-EEEE bug is that a bogus-ref
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


@pytest.fixture
def dirty_indexed_project(tmp_path):
    """Indexed project with a real uncommitted working-tree edit.

    Used to exercise the populated-diff happy path -- ensures the
    positive regression test (clean diff still produces a real
    verdict) actually sees a non-empty changeset.
    """
    proj = tmp_path / "dirty-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\ndef main():\n    return greet('world')\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    # Now edit the file so `git diff` produces a real changeset.
    (proj / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hello {name}'\n"  # changed greeting
        "\n"
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "def farewell(name):\n"  # newly-added symbol
        "    return f'bye {name}'\n"
    )
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash on degenerate diff-source paths.
# ---------------------------------------------------------------------------


class TestDiffSourceNoCrash:
    """Bogus diff-source paths must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C).

    This bucket is already SEALED on cmd_diff via the W641-followup-E
    no-changes envelope (``cmd_diff.py:476-529``). These tests are
    guard-rails that the W805-EEEE fix doesn't accidentally reintroduce
    the empty-stdout crash class while adding the missing state /
    resolution disclosure on top.
    """

    def test_bogus_commit_range_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range: non-empty stdout, parseable JSON, no exception."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"bogus commit-range must exit 0; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus-range"
        # Must parse as JSON
        data = parse_json_output(result, "diff")
        assert isinstance(data, dict)

    def test_bogus_single_ref_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus single ref: non-empty stdout, parseable JSON."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff", "totally_fake_ref"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus ref"
        data = parse_json_output(result, "diff")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# REAL BUG pinned strict.
#
# The bug is STRICTLY MORE SEVERE than W805-AAAA: cmd_diff's
# bogus-commit-range / bogus-single-ref path is INDISTINGUISHABLE
# from a clean working tree. The envelope emits
# ``verdict: "no changes"`` / ``state: "no_changes"`` /
# ``partial_success: false`` -- the same envelope shape produced by
# a real clean tree. An agent has NO machine-state field to switch on
# beyond the free-form ``label`` string.
# ---------------------------------------------------------------------------


class TestBogusCommitRangeStateDisclosure:
    """The bogus-commit-range path produces a ``state: "no_changes"``
    envelope indistinguishable from a clean working tree. There is NO
    git_error field, NO partial_success: true, NO warnings_out array,
    NO resolution field. An agent acting on the verdict concludes the
    diff is safe."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-EEEE REAL BUG: src/roam/commands/cmd_diff.py:475-529 "
            "(the ``if not changed:`` branch downstream of "
            '``get_changed_files``) emits ``verdict: "no changes"`` + '
            '``state: "no_changes"`` + ``partial_success: false`` on a '
            "bogus commit-range -- byte-identical to the clean-tree "
            "envelope. The root cause is "
            "``src/roam/commands/changed_files.py:142,145`` swallowing "
            "``returncode != 0`` / FileNotFoundError / TimeoutExpired into "
            "an empty list. Pattern-1-V-D silent-success-on-degraded-"
            "resolution: a typo'd ref produces the strongest possible "
            "safety signal. STRICTLY MORE SEVERE than W805-AAAA which at "
            'least discloses ``git_error: "git_error"`` + '
            "``partial_success: true``. Pinned strict; graduates when the "
            "bogus-ref path emits ``state`` with a non-``no_changes`` "
            "closed-enum value (e.g. ``git_error`` / ``unknown_ref`` / "
            "``unresolved_diff_source``)."
        ),
    )
    def test_bogus_commit_range_state_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range path must emit a non-``no_changes`` ``state``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "diff")
        summary = data["summary"]
        state = summary.get("state")
        # The bug: state == "no_changes" -- byte-identical to clean tree.
        # The fix: a closed-enum state token that distinguishes bogus-ref
        # from a real clean tree. Reject "no_changes" explicitly.
        assert state and state != "no_changes", (
            f"W805-EEEE Pattern-1-V-D: bogus-commit-range path must emit "
            f"a non-``no_changes`` summary.state to distinguish typo'd "
            f"ref from genuinely clean working tree; got {state!r}"
        )


class TestBogusBaseRefResolutionDisclosure:
    """The bogus single-ref path (e.g. ``roam diff totally_fake_ref``)
    is the second user-error class that collapses into the ``no_changes``
    envelope. Same Pattern-1-V-D gap as the bogus-commit-range path.

    cmd_diff does not have a ``--base-ref`` flag (that's cmd_delete_check's
    surface). On cmd_diff the analogous user error is a bogus positional
    COMMIT_RANGE that resolves through ``git diff <ref>`` to nothing."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-EEEE REAL BUG (mirror): a bogus positional ref "
            "(``roam diff totally_fake_ref``) emits the same "
            '``state: "no_changes"`` envelope as a clean tree. '
            "Pattern-1-V-D resolution-disclosure gap on the second "
            "user-error axis. Pinned strict; graduates when the "
            "envelope distinguishes bogus-ref from clean-tree."
        ),
    )
    def test_bogus_single_ref_resolution_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus single ref must emit ``summary.resolution`` OR a non-``no_changes`` state."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff", "totally_fake_ref"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "diff")
        summary = data["summary"]
        # Either a resolution field OR a distinguishing state is acceptable
        # as a fix -- the Pattern-1-V-D contract requires AT LEAST ONE
        # closed-enum disclosure beyond the free-form label string.
        resolution = summary.get("resolution")
        state = summary.get("state")
        assert (resolution and isinstance(resolution, str) and resolution.strip()) or (
            state and state != "no_changes"
        ), (
            f"W805-EEEE Pattern-1-V-D: bogus single-ref path must emit "
            f"summary.resolution OR a non-``no_changes`` summary.state; "
            f"got resolution={resolution!r} state={state!r}"
        )


class TestGitErrorEnvelopeStateField:
    """Pattern-2 disclosure: when ``get_changed_files`` returns ``[]`` due
    to a git failure (not a clean tree), the envelope must surface a
    distinct error class. Currently it does NOT -- the same envelope
    shape covers both branches.

    This test pins the missing ``git_error`` field on the failure path.
    It is the closest analogue to cmd_delete_check's existing
    ``summary.git_error`` -- the W805-AAAA gap is missing the
    ``state``/``resolution`` ABOVE that field. The W805-EEEE gap is
    missing the field ENTIRELY."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-EEEE REAL BUG: cmd_diff's bogus-ref path emits no "
            "``git_error`` field at all -- a STRICTLY WORSE shape than "
            "cmd_delete_check's W805-AAAA gap. The shared "
            "``get_changed_files`` helper at "
            "``src/roam/commands/changed_files.py:131-146`` returns "
            "``[]`` on three distinct failure classes (returncode != 0, "
            "FileNotFoundError, TimeoutExpired) -- cmd_diff has no way "
            "to distinguish them from a real clean tree. The Pattern-2 "
            "silent-fallback contract requires a loud sentinel on the "
            "degraded path. Pinned strict; graduates when "
            "``get_changed_files`` returns a ``(paths, error_kind)`` "
            "tuple (mirroring ``_git_diff`` in cmd_delete_check) and "
            "cmd_diff surfaces ``summary.git_error`` on the failure "
            "branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref path must emit ``summary.git_error`` distinct from clean tree."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "diff")
        summary = data["summary"]
        # The W805-AAAA-analogue field on cmd_diff. Currently absent.
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-EEEE: bogus-ref path must emit summary.git_error "
            f"distinct from clean tree (which has no git failure); "
            f"got {git_error!r}"
        )


class TestEmptyDiffDistinctFromGitError:
    """Pattern-2 (positive direction): a genuinely-clean working tree and
    a git-error path MUST produce distinguishable envelopes. Today they
    are byte-identical except for ``label``. This is the core invariant
    the W805-EEEE fix must preserve.

    The test compares the two paths and asserts they differ on at least
    one machine-state field (state, resolution, git_error, or
    partial_success). Pinned strict because the current shape fails it."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-EEEE REAL BUG (invariant): clean-tree envelope and "
            "bogus-ref envelope are byte-identical on every "
            "machine-state field (state, resolution, git_error, "
            "partial_success). The only differing field is ``label`` "
            "(free-form string). Pattern-2 silent-fallback contract "
            "violated. Pinned strict; graduates when the two envelopes "
            "differ on at least one closed-enum machine-state field."
        ),
    )
    def test_clean_tree_distinct_from_bogus_ref(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean-tree envelope must differ from bogus-ref envelope on a machine-state field."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["diff"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_result = invoke_cli(
            cli_runner,
            ["diff", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_summary = parse_json_output(clean_result, "diff")["summary"]
        bogus_summary = parse_json_output(bogus_result, "diff")["summary"]

        # Compare every machine-state field. At least ONE must differ.
        machine_state_fields = ("state", "resolution", "git_error", "partial_success")
        differing = [f for f in machine_state_fields if clean_summary.get(f) != bogus_summary.get(f)]
        assert differing, (
            f"W805-EEEE Pattern-2: clean-tree and bogus-ref envelopes "
            f"must differ on at least one machine-state field "
            f"({machine_state_fields}); got identical values "
            f"clean={ {f: clean_summary.get(f) for f in machine_state_fields} } "
            f"bogus={ {f: bogus_summary.get(f) for f in machine_state_fields} }"
        )


# ---------------------------------------------------------------------------
# W805-AAAA cross-check -- the sister suite's invariants must stay green.
# This is a guard-rail that the W805-EEEE pin doesn't accidentally
# perturb the cmd_delete_check shape.
# ---------------------------------------------------------------------------


class TestW805AaaaInvariantsPreserved:
    """Cross-check: cmd_delete_check's W805-AAAA shape is preserved.
    A future W805-EEEE fix to ``get_changed_files`` SHOULD NOT break
    cmd_delete_check's existing ``_git_diff``-helper contract."""

    def test_delete_check_git_error_field_still_emitted(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_delete_check still emits ``summary.git_error`` on bogus ref.

        This is the W607-J + W805-AAAA combined contract: even though
        W805-AAAA pins state/resolution as MISSING, the existing
        git_error field MUST stay -- it's the only current disclosure
        an agent can switch on for cmd_delete_check.
        """
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
        # The W805-AAAA fixture confirms summary.git_error == "git_error".
        # Cross-check it stays present so W805-EEEE doesn't accidentally
        # remove it via a shared-helper change.
        git_error = summary.get("git_error")
        assert git_error == "git_error", (
            f"W805-EEEE cross-check: cmd_delete_check's existing "
            f"summary.git_error contract must stay; got {git_error!r}"
        )


# ---------------------------------------------------------------------------
# Positive regression -- clean diff sources still produce real verdicts.
# Guards against an over-correcting fix-forward.
# ---------------------------------------------------------------------------


class TestResolutionFieldOnCleanDiff:
    """Positive regression: a real populated diff (no bogus ref) still
    produces a real verdict with the existing envelope shape."""

    def test_real_diff_produces_real_verdict(self, cli_runner, dirty_indexed_project, monkeypatch):
        """A real working-tree edit produces a real blast-radius verdict."""
        monkeypatch.chdir(dirty_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["diff"],
            cwd=dirty_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "diff")
        summary = data["summary"]
        verdict = summary.get("verdict") or ""
        # Real-diff verdict is "<n> files changed, <m> symbols affected, ..."
        # NOT the empty-state "no changes".
        assert "no changes" not in verdict.lower(), (
            f"Positive regression: real-diff verdict must NOT be ``no changes``; got {verdict!r}"
        )
        assert summary.get("state") != "no_changes", (
            f"Positive regression: real-diff envelope must NOT carry state=no_changes; got {summary.get('state')!r}"
        )
        # changed_files > 0 on a real edit
        assert summary.get("changed_files", 0) > 0, (
            f"Positive regression: real-diff envelope must report "
            f"changed_files > 0; got {summary.get('changed_files')!r}"
        )

    def test_clean_tree_still_emits_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """Positive regression: clean tree still emits the canonical
        ``no_changes`` envelope. This is the pre-W805-EEEE contract
        pinned in ``test_diff_empty_state.py`` -- it must stay even
        after the bogus-ref path is disambiguated."""
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
        # The existing W641-followup-E contract -- clean tree IS "no_changes".
        assert summary.get("verdict") == "no changes"
        assert summary.get("state") == "no_changes"
        assert summary.get("partial_success") is False
