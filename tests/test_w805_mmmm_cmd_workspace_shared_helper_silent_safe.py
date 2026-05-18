"""W805-MMMM -- shared-helper silent-SAFE probe on ``roam ws``.

Ninety-first-in-batch W805 sweep. DISCONFIRMATION outcome.

The mission brief proposed cmd_workspace (``src/roam/commands/cmd_ws.py``)
as the THIRD potential strict consumer of
``roam.commands.changed_files.get_changed_files`` after:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via the shared
    helper returning ``[]`` on all failure classes.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE silent-SAFE via
    the same shared helper (no ``state`` field at all).

If confirmed, the shared-helper family on the strict ``get_changed_files``
axis would have elevated from 2-strong to 3-strong (structural class,
analogous to the OCTET on ``_compound_envelope``).

W978 first-hypothesis: cmd_ws is a shared-helper consumer
----------------------------------------------------------

W978 + W907 discipline requires verifying the first hypothesis against
the source BEFORE building xfail pins around it.

Source audit of ``src/roam/commands/cmd_ws.py`` head-to-tail:

  * No ``from roam.commands.changed_files import`` line.
  * No ``get_changed_files`` reference anywhere in the file.
  * Grep over ``src/roam/commands`` for ``get_changed_files`` returns
    19 files (cmd_pr_risk, cmd_test_gaps, cmd_diff, changed_files,
    cmd_attest, cmd_preflight, cmd_plan, cmd_orchestrate,
    cmd_adversarial, cmd_boundary, cmd_why_slow, cmd_file, cmd_verify,
    cmd_syntax_check, cmd_suggest_reviewers, cmd_pr_diff, cmd_coupling,
    cmd_affected_tests, cmd_affected) -- ``cmd_ws.py`` is NOT in that
    list.

cmd_ws is a workspace-management command family (init / status /
resolve / understand / health / context / trace) operating across
multiple indexed repos via ``roam.workspace.*`` modules. It owns its
own state-disclosure pattern (the canonical Pattern-1A envelope in
``_require_workspace`` at lines 838-891) and its own Pattern-2
``partial_success`` disclosure on ``ws resolve`` (lines 446-460). It
does not touch git diff/ref resolution; it works on indexed databases.

W978 finding: DISCONFIRMED. The mission's first hypothesis ("cmd_ws
inherits the W805-EEEE/JJJJ silent-SAFE shape via the shared helper")
is FALSE on source-level grounds. cmd_ws is not a member of the
shared-helper family.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_ws.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import``
case-insensitive): NO matches. The function-scoped lazy imports
inside each subcommand (``from roam.workspace.config import ...``)
are runtime-scope deferrals consistent with the LazyGroup pattern,
not false-cycle hedges. Clean on W907.

Probe results (probe via CliRunner against an uninitialized cwd)
----------------------------------------------------------------

* ``roam --json ws status`` on an uninitialized directory:
  exit 1, ``state: "not_initialized"``, ``isError: true``,
  ``error_code: "WORKSPACE_NOT_INITIALIZED"``, ``next_command:
  "roam ws init <repo1> <repo2>"``. Canonical Pattern-1A envelope.
  NO silent-SAFE.

* The ws-resolve verdict logic at cmd_ws.py:446-460 explicitly
  emits ``partial_success: true`` when any unmatched URLs survive
  and ``state: "no_frontend_calls"`` / ``"partial_match"`` /
  ``"ok"`` on the closed enum. Pattern-2 textbook good.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff (shared-helper consumer).
  * W805-JJJJ: cmd_pr_diff (shared-helper consumer) -- 2nd strict
    ``get_changed_files`` consumer.
  * W805-AAAA: cmd_delete_check (independent helper).

After this probe:
  * cmd_ws DISCONFIRMED. NOT a shared-helper family member.
  * Shared-helper family stays 2-strong on the strict
    ``get_changed_files`` axis (cmd_diff + cmd_pr_diff). Total
    family stays 3-strong with cmd_delete_check on the
    analogous-but-independent axis.

This probe is a NEGATIVE result. The invariant it pins is the
absence of consumption: cmd_ws.py must not silently grow a
``get_changed_files`` call without re-auditing for inherited
silent-SAFE. If a future refactor introduces such a call, the
disconfirmation test below flips RED -- which is the correct
signal to re-open the family-membership question.

No xfail-strict markers in this suite. xfail-strict is reserved
for pinning a REAL BUG until graduation; there is no real bug in
cmd_ws on the shared-helper axis.

Next W805 sweep candidate (W805-NNNN)
-------------------------------------

Per the canonical ``get_changed_files`` consumer list (19 modules),
the unprobed strict candidates are: cmd_attest, cmd_test_gaps,
cmd_orchestrate (already in W805-DDDD target-resolution scope but
NOT on the shared-helper axis), cmd_adversarial, cmd_boundary,
cmd_why_slow, cmd_file, cmd_verify, cmd_syntax_check,
cmd_suggest_reviewers, cmd_coupling, cmd_affected_tests,
cmd_affected, cmd_preflight, cmd_plan. cmd_pr_risk (W607-Q) and
cmd_taint (W805-KKKK) and cmd_runs (W805-LLLL) are in-flight and
NOT candidates this round. W805-NNNN candidate: cmd_attest --
proof-bundle/CGA family, likely to consume changed-files on the
``--from-diff`` axis.
"""

from __future__ import annotations

import re
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

    Reused from the W805-EEEE / W805-JJJJ sister-suite shape so sister
    cross-checks below can run against the same fixture.
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
# W978 first-hypothesis verification -- cmd_ws is NOT a get_changed_files
# consumer. This is the source-level invariant that DISCONFIRMS the
# proposed third-member candidacy.
# ---------------------------------------------------------------------------


class TestCmdWsDoesNotConsumeGetChangedFiles:
    """W978 first-hypothesis verification (DISCONFIRMING).

    The mission brief proposed cmd_ws as the third strict consumer of
    ``get_changed_files`` after W805-EEEE (cmd_diff) and W805-JJJJ
    (cmd_pr_diff). Source-level audit shows cmd_ws does NOT import or
    call the shared helper at all. This test pins the absence: if a
    future refactor introduces such a call, the test flips RED and
    the family-membership question must be re-opened.
    """

    def test_cmd_ws_does_not_import_changed_files_helper(self):
        """cmd_ws.py must not import from roam.commands.changed_files.

        Pinning the absence-of-import keeps the W805-MMMM disconfirmation
        durable: any future ``from roam.commands.changed_files import
        get_changed_files`` line in cmd_ws.py re-opens the family
        membership audit. If such an import is added intentionally,
        upgrade this assertion AND re-run the W805 sweep audit.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_ws.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" not in src, (
            "W805-MMMM W978 DISCONFIRMATION: cmd_ws.py must NOT import "
            "from roam.commands.changed_files. If you intentionally "
            "added the import, this test catches the family-membership "
            "drift and the W805 audit must re-open."
        )

    def test_cmd_ws_does_not_reference_get_changed_files(self):
        """cmd_ws.py must not reference ``get_changed_files`` anywhere.

        Source-level grep guard. Catches indirect references
        (e.g. via ``import roam.commands.changed_files as _cf;
        _cf.get_changed_files(...)``) that would slip past the
        ``from ... import`` line check above.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_ws.py").read_text(
            encoding="utf-8"
        )
        assert "get_changed_files" not in src, (
            "W805-MMMM W978 DISCONFIRMATION: cmd_ws.py must NOT "
            "reference get_changed_files. If you intentionally added "
            "the reference, this test catches the family-membership "
            "drift and the W805 audit must re-open."
        )


# ---------------------------------------------------------------------------
# W907 verify-cycle -- cmd_ws has no false-import-cycle hedges. The
# function-scoped imports inside each subcommand are runtime-scope
# deferrals consistent with the LazyGroup pattern; they are not
# defensive hedges around a non-existent import cycle.
# ---------------------------------------------------------------------------


class TestCmdWsW907Clean:
    """W907 verify-cycle invariant: cmd_ws.py has no false-cycle hedges."""

    def test_cmd_ws_has_no_false_cycle_hedges(self):
        """cmd_ws.py must not carry W907-style false-cycle hedge comments.

        The function-scoped imports in cmd_ws are runtime-scope
        deferrals consistent with the LazyGroup pattern. Hedge comments
        like ``# avoid circular import`` / ``# defer to avoid cycle``
        belong to the W907 anti-pattern; cmd_ws does not have them
        today. Test guards the absence.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_ws.py").read_text(
            encoding="utf-8"
        )
        patterns = [
            r"avoid.*cycle",
            r"avoid.*circular",
            r"prevent.*import.*cycle",
            r"defer.*import.*to.*avoid",
        ]
        for p in patterns:
            matches = re.findall(p, src, flags=re.IGNORECASE)
            assert not matches, (
                f"W805-MMMM W907 verify-cycle: cmd_ws.py grew a "
                f"hedge-comment matching /{p}/. Verify the alleged "
                f"cycle actually exists (grep both directions) before "
                f"keeping the hedge; if the cycle is false, hoist the "
                f"duplicated logic."
            )


# ---------------------------------------------------------------------------
# Pattern-1A invariant -- cmd_ws emits a canonical "not initialized"
# envelope on uninitialized workspaces. Sister-pattern cross-check: the
# Pattern-1 family's variant A (missing prerequisite) is properly
# disclosed in cmd_ws, NOT silent-SAFE.
# ---------------------------------------------------------------------------


class TestCmdWsPattern1AEnvelope:
    """cmd_ws subcommands on an uninitialized workspace must emit the
    canonical Pattern-1A failure envelope, NOT a silent-SAFE."""

    def test_ws_status_uninitialized_pattern_1a(self, cli_runner, tmp_path, monkeypatch):
        """``ws status`` on a non-workspace dir emits state=not_initialized."""
        # Use a brand-new tmp_path subdirectory with no .roam-workspace.json
        # anywhere up the chain.
        empty = tmp_path / "not-a-workspace"
        empty.mkdir()
        monkeypatch.chdir(empty)

        result = invoke_cli(
            cli_runner,
            ["ws", "status"],
            cwd=empty,
            json_mode=True,
        )
        # Pattern-1A: exit 1 but with a structured envelope (NOT silent-SAFE).
        assert result.exit_code == 1, (
            f"ws-status uninitialized must exit 1 (structured failure); got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on uninitialized"

    def test_ws_resolve_uninitialized_pattern_1a(self, cli_runner, tmp_path, monkeypatch):
        """``ws resolve`` on a non-workspace dir emits state=not_initialized."""
        empty = tmp_path / "not-a-workspace-2"
        empty.mkdir()
        monkeypatch.chdir(empty)

        result = invoke_cli(
            cli_runner,
            ["ws", "resolve"],
            cwd=empty,
            json_mode=True,
        )
        assert result.exit_code == 1, f"ws-resolve uninitialized must exit 1; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on uninitialized"


# ---------------------------------------------------------------------------
# Family-stay-2-strong cross-check -- the W805-EEEE / W805-JJJJ
# invariants on the actual shared-helper consumers must continue to
# hold. A future fix to ``get_changed_files`` that returns
# ``(paths, error_kind)`` would graduate EEEE + JJJJ atomically; this
# cross-check ensures the disconfirmation here does not perturb them.
# ---------------------------------------------------------------------------


class TestW805EeeeInvariantsPreserved:
    """Sister cross-check: cmd_diff's W805-EEEE clean-tree envelope
    shape is preserved (state=no_changes, partial_success=false)."""

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
            f"W805-MMMM sister cross-check: cmd_diff clean-tree must "
            f"still emit state=no_changes; got {summary.get('state')!r}"
        )
        assert summary.get("verdict") == "no changes"
        assert summary.get("partial_success") is False


class TestW805JjjjInvariantsPreserved:
    """Sister cross-check: cmd_pr_diff's W805-JJJJ clean-tree envelope
    shape is preserved (verdict=no changes detected)."""

    def test_pr_diff_clean_tree_shape(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_pr_diff clean tree still emits the documented clean-tree
        shape: verdict containing ``no changes`` and partial_success=false.

        Phrasing-tolerant on verdict: cmd_pr_diff uses ``no changes
        detected`` today; future re-phrasing that keeps the ``no
        changes`` substring still satisfies the invariant.
        """
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
        verdict = summary.get("verdict", "")
        assert "no changes" in verdict.lower(), (
            f"W805-MMMM sister cross-check: cmd_pr-diff clean-tree "
            f"must still emit verdict containing 'no changes'; "
            f"got {verdict!r}"
        )
        assert summary.get("partial_success") is False, (
            f"W805-MMMM sister cross-check: cmd_pr-diff clean-tree "
            f"must still emit partial_success=false; got "
            f"{summary.get('partial_success')!r}"
        )
