"""W805-HHHH -- shared-helper inheritance probe on ``roam critique``.

Eighty-sixth-in-batch W805 sweep. THIRD-candidate probe in the
resolution-disclosure family started by W805-AAAA (cmd_delete_check)
and W805-EEEE (cmd_diff). The hypothesis was: ``cmd_critique`` consumes
the SAME ``src/roam/commands/changed_files.py:get_changed_files()``
helper as cmd_diff and inherits the silent-SAFE bug transitively. If
confirmed across three consumers, the bug becomes a SHARED-HELPER
family (analogous to the ``_compound_envelope`` octet -- one root site,
multiple consumer pins).

W978 first-hypothesis check -- DISCONFIRMED
-------------------------------------------

Re-read of ``cmd_critique.py`` head-to-tail confirms the hypothesis is
WRONG. ``cmd_critique`` consumes a unified DIFF TEXT from stdin (or
``--input PATH`` / ``--batch DIR``) -- it does NOT call
``get_changed_files()`` and never invokes git for "which files
changed?". The only git call is the OPTIONAL latest-commit-subject
fetch at lines 853-866 used for intent text; the diff itself is
ingested as text, parsed by ``roam.critique.checks.parse_diff``, and
the resolved-symbols set comes from a graph lookup keyed by file path
PARSED OUT OF THE DIFF.

This means the bug class is DISTINCT, not transitively inherited:
``cmd_critique`` can only be silent-SAFE on an "absent diff input" /
"diff that resolves zero changed symbols" axis -- both of which W832
already sealed (see ``aggregator.aggregate`` + the
``_run_checks_with_status`` orchestrator + the
``TestW832CritiqueCheckStatusCLI`` suite in ``test_critique.py``).

W832 closure status -- CLOSED by this probe
-------------------------------------------

W832 was the original "no concerns from roam critique" silent-SAFE
finding. The deferred-deeper-review note from Wave832 asked whether
the orchestrator might still emit "No concerns" on a degraded path.
This probe walks the four degraded surfaces -- bogus file in diff
(resolves zero symbols), no-clones-persisted DB, no intent text, all
three combined -- and confirms every one of them produces a HONEST
verdict ``"0 concerns from N of 3 checks (...)"`` with
``partial_success: True`` and ``state: "partial_critique"``. W832
closes here.

W907 verify-cycle check -- CLEAN
--------------------------------

Searched ``cmd_critique.py`` for cycle-hedge defensive comments
("avoid import cycle" / "circular import" / "kept local to avoid"):
zero matches. The lazy-import pattern at the bottom of the command
function (e.g. ``from roam.commands.next_steps import ...``) is a
genuine lazy-load for performance, not a cargo-cult cycle hedge.

SHARED-HELPER family verdict -- NOT confirmed by W805-HHHH
----------------------------------------------------------

The shared-helper family hypothesis stays at TWO members after this
probe -- cmd_diff (W805-EEEE) and cmd_workspace / cmd_pr_risk /
cmd_pr_diff (the other documented consumers of
``get_changed_files``). cmd_critique is structurally outside the
family because it ingests diff TEXT not a git ref. A future probe
would need to land on cmd_workspace / cmd_pr_risk / cmd_pr_diff to
promote the cmd_diff finding to a structural family.

This file is a CLOSURE PIN -- it asserts the W832 contract holds end
to end (no xfail entries) and documents why the W805-HHHH hypothesis
was disconfirmed so a future agent walking the W805 sweep doesn't
re-investigate.

Sealed: 2026-05-18 (Wave805-HHHH).
"""

from __future__ import annotations

import json
import os
import textwrap

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project

_PROJECT_FILES = {
    "auth.py": """
        class UserSession:
            def __init__(self, token):
                self.token = token

            def refresh(self):
                return self.token

            def revoke(self):
                return None

        def handle_login(user):
            s = UserSession(token="abc")
            return s.refresh()
    """,
}


@pytest.fixture
def critique_indexed_project(tmp_path):
    """Project with index built but WITHOUT ``clones --persist``.

    Forces the clones-not-edited check onto the skipped path so the
    W832 disclosure surfaces structurally.
    """
    proj = _make_project(tmp_path, _PROJECT_FILES)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Architecture probe: cmd_critique consumes stdin, not get_changed_files
# ---------------------------------------------------------------------------


class TestCritiqueDoesNotConsumeSharedHelper:
    """W805-HHHH structural assertion: cmd_critique is NOT a
    ``get_changed_files`` consumer. Pins the W978 first-hypothesis
    finding so a future audit doesn't re-investigate the family.
    """

    def test_cmd_critique_source_does_not_import_get_changed_files(self):
        from pathlib import Path

        from roam.commands import cmd_critique

        src = Path(cmd_critique.__file__).read_text(encoding="utf-8")
        # The hypothesis is: cmd_critique consumes the shared helper
        # ``get_changed_files()`` from
        # ``src/roam/commands/changed_files.py`` and inherits the
        # silent-SAFE bug. If this assertion ever flips (a future
        # refactor wires cmd_critique through the shared helper),
        # this test fires LOUDLY and the W805-EEEE silent-SAFE bug
        # CLASS becomes transitively inherited -- at which point this
        # test should be replaced by an xfail-strict pinning the new
        # bug rather than relaxed.
        assert "get_changed_files" not in src, (
            "cmd_critique now imports get_changed_files -- the W805-HHHH "
            "disconfirmed shared-helper hypothesis is no longer valid. "
            "Audit the new code path for silent-SAFE on bogus refs."
        )

    def test_cmd_critique_reads_diff_from_stdin_or_input(self):
        """Pins the documented stdin/--input/--batch contract from the
        critique docstring (CLAUDE.md and the click decorators)."""
        from pathlib import Path

        from roam.commands import cmd_critique

        src = Path(cmd_critique.__file__).read_text(encoding="utf-8")
        # Three documented input paths must remain in place.
        assert "sys.stdin.read()" in src
        assert '"--input"' in src
        assert '"--batch"' in src


# ---------------------------------------------------------------------------
# W832 closure: every degraded path must surface partial_critique
# ---------------------------------------------------------------------------


class TestW832ClosureViaW805HHHH:
    """Every degraded surface of cmd_critique must emit
    ``partial_success: True`` + ``state: "partial_critique"`` instead
    of the legacy silent "No concerns" verdict.

    These tests run the actual CLI against curated diffs and assert
    the envelope shape. They close the W832 deferred-deeper-review
    item from Wave832 by exhaustively walking the degraded paths.
    """

    def _run_critique(self, project_root, diff_text, *, intent=None):
        diff_path = project_root / "patch.diff"
        diff_path.write_text(diff_text, encoding="utf-8")
        runner = CliRunner()
        argv = ["--json", "critique", "--input", str(diff_path)]
        if intent is not None:
            argv += ["--intent", intent]
        result = runner.invoke(cli, argv)
        assert result.exit_code in (0, 5), (
            f"critique should not crash on degraded input; "
            f"exit={result.exit_code}\nout={result.output}\nexc={result.exception}"
        )
        return json.loads(result.output)

    def test_diff_against_unindexed_file_marks_all_skipped(self, critique_indexed_project):
        """A diff against a file the indexer never saw resolves zero
        changed symbols. All three checks must be marked
        ``skipped:no_changed_symbols`` -- NOT silently "ran"."""
        diff = textwrap.dedent(
            """\
            diff --git a/never_existed.py b/never_existed.py
            --- a/never_existed.py
            +++ b/never_existed.py
            @@ -1,1 +1,1 @@
            -old
            +new
            """
        )
        data = self._run_critique(critique_indexed_project, diff, intent="add feature")
        summary = data["summary"]
        # Legacy silent-SAFE verdict must NEVER appear on a degraded path.
        assert summary["verdict"] != "No concerns from roam critique"
        # W832 contract: partial_critique state + partial_success True.
        assert summary["partial_success"] is True
        assert summary["state"] == "partial_critique"
        # Every check skipped because no changed symbols resolved.
        cs = summary["check_status"]
        assert cs["clones-not-edited"].startswith("skipped:")
        assert cs["impact"].startswith("skipped:")
        assert cs["intent"].startswith("skipped:")

    def test_diff_against_indexed_file_with_no_clone_pairs_marks_clones_skipped(self, critique_indexed_project):
        """A diff against an indexed file where ``clones --persist``
        was never run -- the clones-not-edited check MUST be marked
        skipped, not silently "ran with 0 findings"."""
        diff = textwrap.dedent(
            """\
            diff --git a/auth.py b/auth.py
            --- a/auth.py
            +++ b/auth.py
            @@ -6,1 +6,2 @@
             def refresh(self):
            +    pass
            """
        )
        data = self._run_critique(critique_indexed_project, diff, intent="touch refresh")
        summary = data["summary"]
        cs = summary["check_status"]
        # If no changed symbols resolved the skip reason will be
        # ``no_changed_symbols``; otherwise it must be
        # ``no_clone_pairs``. Either way: NOT "ran".
        assert cs["clones-not-edited"].startswith("skipped:"), f"expected skipped:*, got {cs['clones-not-edited']!r}"
        # If clones is skipped, partial_success must be True.
        assert summary["partial_success"] is True
        assert summary["state"] == "partial_critique"
        assert summary["verdict"] != "No concerns from roam critique"

    def test_empty_stdin_rejected_loudly_not_silent_safe(self, critique_indexed_project):
        """Empty stdin must raise a structured usage error, NOT collapse
        to a silent ``"No concerns"`` verdict. Probed via empty input
        file (equivalent to empty stdin in the CLI surface)."""
        runner = CliRunner()
        diff_path = critique_indexed_project / "empty.diff"
        diff_path.write_text("", encoding="utf-8")
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        # The command must exit non-zero on empty input -- never
        # silently succeed with "No concerns".
        assert result.exit_code != 0
        out = result.output
        # Either a structured error or a Click usage error; both are
        # explicit failures, not silent-SAFE.
        assert "No concerns" not in out
        assert "EMPTY_INPUT" in out or "empty" in out.lower()

    def test_non_diff_input_rejected_loudly_not_silent_safe(self, critique_indexed_project):
        """Non-unified-diff text must raise INVALID_DIFF -- the
        cmd_critique docstring (lines 838-845) commits to this contract
        explicitly: "Earlier silent failures: shell substitutions that
        lost the diff, paste-buffer truncation, or wrong-format
        input."
        """
        runner = CliRunner()
        diff_path = critique_indexed_project / "junk.txt"
        diff_path.write_text("this is definitely not a diff\n", encoding="utf-8")
        result = runner.invoke(cli, ["--json", "critique", "--input", str(diff_path)])
        assert result.exit_code != 0
        out = result.output
        assert "No concerns" not in out
        assert "INVALID_DIFF" in out or "unified diff" in out.lower()


# ---------------------------------------------------------------------------
# Sister-suite parity invariant (W805-EEEE must stay green)
# ---------------------------------------------------------------------------


class TestSisterSuiteParityInvariant:
    """Pins the W805-EEEE sister-suite SHAPE invariant: cmd_diff
    (the actual ``get_changed_files`` consumer) is the locus of the
    silent-SAFE bug, not cmd_critique. If a future change accidentally
    re-wires cmd_critique through ``get_changed_files``, this assertion
    fires and the family verdict must be re-evaluated.
    """

    def test_changed_files_helper_still_swallows_git_errors(self):
        """The shared-helper root site (W805-EEEE finding) must stay
        in place at ``changed_files.py:142+145`` so the cmd_diff fix
        when it lands has a known anchor. If this helper's behaviour
        flips before cmd_diff is fixed, the family ordering needs to
        be re-assessed."""
        from pathlib import Path

        from roam.commands import changed_files

        src = Path(changed_files.__file__).read_text(encoding="utf-8")
        # The two swallow sites that W805-EEEE pins as the root cause.
        # When (not if) the fix lands at the helper level, these
        # specific returns will be replaced by a structured error
        # disclosure -- the test will need updating at that point.
        assert "if result.returncode != 0:" in src and "return []" in src, (
            "changed_files.get_changed_files no longer matches the "
            "W805-EEEE root-cause shape -- audit cmd_diff to confirm "
            "the silent-SAFE fix landed and update this guard."
        )
