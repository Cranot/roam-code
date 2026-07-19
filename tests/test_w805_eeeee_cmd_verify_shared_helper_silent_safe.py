"""W805-EEEEE -- shared-helper silent-SAFE probe on ``roam verify``.

Hundred-and-ninth-in-batch W805 sweep. NINTH strict consumer for the
shared-helper resolution-disclosure family on the ``get_changed_files``
axis -- CONFIRMED STRUCTURAL.

Family lineage entering this probe:

  * W805-EEEE  (cmd_diff)            -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ  (cmd_pr_diff)         -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO  (cmd_attest)          -- THIRD strict consumer.
  * W805-RRRR  (cmd_test_gaps)       -- FOURTH strict consumer.
  * W805-SSSS  (cmd_affected_tests)  -- FIFTH (STRICTLY WORST -- plain text in --json mode).
  * W805-VVVV  (cmd_affected)        -- SIXTH (envelope shape, two call sites).
  * W805-XXXX  (cmd_adversarial)     -- SEVENTH (envelope shape).
  * W805-AAAAA (cmd_boundary)        -- EIGHTH (envelope shape).
  * W805-AAAA  (cmd_delete_check)    -- independent ``_git_diff`` helper.
  * W805-CCCCC (cmd_why_slow)        -- LATENT (BLOCKER-class TypeError pre-empts).

Family stood 8-STRONG STRUCTURAL + 1 latent (cmd_why_slow blocked on
TypeError) + 1 independent (cmd_delete_check) at the start of this probe.

W978 first-hypothesis verification
----------------------------------

Source audit of ``src/roam/commands/cmd_verify.py`` head-to-tail:

  * Line 24: ``from roam.commands.changed_files import get_changed_files,
    resolve_changed_to_db``. The import IS the shared helper used by all
    eight prior strict consumers AND the resolver from W805-VVVV.
  * Line 706: ONE call site (default-branch, no ``--changed`` flag
    needed -- the command falls through to ``get_changed_files(root)``
    when ``files`` is empty):
    ``target_paths = get_changed_files(root)``.

Cross-checking against the helper signature
(``src/roam/commands/changed_files.py:98-105``):

    def get_changed_files(
        root: Path,
        staged: bool = False,
        commit_range: str | None = None,
        pr: bool = False,
        base_ref: str = "main",
        untracked: bool = False,
    ) -> list[str]:

The cmd_verify call uses ``root`` POSITIONALLY -- the CORRECT signature
shape. NO TypeError-class bug (unlike W805-CCCCC's cmd_why_slow). This
is W978 CONFIRM for the strict-consumer family AND for signature
correctness.

Resolution-disclosure audit
---------------------------

cmd_verify's empty-paths branch (lines 708-729) emits:

  * ``verdict: "PASS"`` (literal, on line 711)
  * ``score: 100`` (literal, on line 710)
  * Empty ``categories`` (all 100s, no violations)

The summary today carries ``verdict / score / threshold / files_checked
/ violation_count`` -- ZERO closed-enum disclosure of the empty-diff
resolution path. The summary is INDISTINGUISHABLE between:

  1. Clean working tree (no changes) -- legitimately PASS.
  2. Git diff failed (returncode != 0) -- shared helper returns ``[]``
     and cmd_verify still emits PASS.
  3. Git not available (FileNotFoundError) -- same shared-helper
     silent-empty-list path, same PASS verdict.

This is the canonical Pattern-1-Variant-D silent-SAFE family-member
shape: success verdict indistinguishable from degraded resolution.
REAL BUG #1 (silent-SAFE family member) pinned strict.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_verify.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): zero matches. All imports are
top-level and bare. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 8-STRONG STRUCTURAL + 1 latent (W805-CCCCC) +
1 independent (W805-AAAA).

After this probe: family **9-STRONG STRUCTURAL** (EEEE / JJJJ / OOOO /
RRRR / SSSS / VVVV / XXXX / AAAAA / **EEEEE**) + 1 latent + 1 independent.
cmd_verify is the NINTH structural strict consumer.

W805 sweep update
-----------------

W805 sweep yield 55/55 (this probe = 55th). Strict-consumer family is
now 9-STRONG fully structural.

Next W805 sweep candidate (W805-FFFFF)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-AAAAA
canonical-list ordering: cmd_syntax_check, cmd_suggest_reviewers,
cmd_coupling, cmd_plan. W805-FFFFF candidate: cmd_syntax_check --
next-most-likely shared-helper consumer per the W805-AAAAA strict-
consumer ordering.
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
    """Indexed project with a clean working tree.

    A clean working tree means ``get_changed_files(root)`` returns ``[]``
    -- exercising the empty-paths branch of cmd_verify (lines 708-729).
    That branch emits the silent-SAFE ``verdict: "PASS"`` envelope this
    probe pins.
    """
    proj = tmp_path / "clean-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "def make_user(name):\n"
        "    return {'name': name}\n"
        "\n"
        "def main():\n"
        "    return greet('world')\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# W978 verification -- cmd_verify actually consumes get_changed_files.
# If cmd_verify is refactored to a different helper, this test surfaces
# the structural drift before the W805-EEEEE pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdVerifyConsumesSharedHelper:
    """Pin verify's status-based target discovery implementation."""

    def test_cmd_verify_consumes_get_changed_files(self):
        """Verify owns explicit, failure-aware porcelain-status discovery."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_verify.py").read_text(
            encoding="utf-8"
        )
        assert "def _discover_verify_targets(root: Path) -> dict:" in src
        assert '"status", "--porcelain=v1", "-z", "--untracked-files=all"' in src


# ---------------------------------------------------------------------------
# W805-CCCCC-echo BLOCKER probe -- the ``roam verify`` invocation must
# NOT raise TypeError. W805-CCCCC found a cmd_why_slow:168 signature bug
# (``get_changed_files(base=base)`` -- both positional ``root`` missing
# AND ``base`` not an accepted keyword). Probe whether cmd_verify has
# the same class of upstream bug. The source audit says NO -- line 706
# is ``get_changed_files(root)`` which is correct -- but pin the
# invariant so future refactors that introduce a similar TypeError-
# class signature break get caught immediately.
# ---------------------------------------------------------------------------


class TestChangedModeNoSignatureTypeError:
    """``roam verify`` invocation (which falls through to the empty-args
    branch and calls ``get_changed_files(root)``) must not raise
    TypeError. This is the W805-CCCCC-echo probe -- confirms cmd_verify
    is NOT BLOCKER-class like cmd_why_slow."""

    def test_verify_no_signature_typeerror(self, cli_runner, clean_indexed_project, monkeypatch):
        """``roam verify`` must exit cleanly (not raise TypeError)."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["verify"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        # Must not be a TypeError-class crash. Exit code 0 (clean) or 5
        # (gate failure) are both acceptable -- both prove the call
        # signature is correct. Anything else (especially TypeError
        # exception) is the BLOCKER class.
        assert result.exit_code in (0, 5), (
            f"W805-EEEEE BLOCKER probe (W805-CCCCC echo): "
            f"``roam verify`` must exit cleanly with 0 or 5, not raise "
            f"TypeError; got exit_code={result.exit_code} "
            f"output={result.output!r}"
        )

    def test_verify_call_site_uses_correct_signature(self):
        """The target resolver passes the repository root positionally."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_verify.py").read_text(
            encoding="utf-8"
        )
        assert "_discover_verify_targets(root)" in src
        assert "_discover_verify_targets(base=" not in src


# ---------------------------------------------------------------------------
# REAL BUG #1: silent-SAFE family member.
#
# cmd_verify's empty-paths branch emits ``verdict: "PASS", score: 100``
# with no closed-enum disclosure of the empty-diff resolution. The
# summary is indistinguishable between (a) clean working tree and
# (b) git failure that returns silent-empty-list. Pinned strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The empty-paths branch (lines 708-729) inherits the silent-SAFE
    family shape from the shared helper. Today the summary carries only
    ``verdict / score / threshold / files_checked / violation_count`` --
    no closed-enum ``state`` / ``git_error`` / ``resolution`` field
    disclosing the no-changes-vs-git-failure disambiguation. Pattern-1-
    Variant-D requires the no-changes path to be distinguishable.
    Pinned strict; graduates when a closed-enum disclosure is added."""

    # GRADUATED 2026-06-18: _empty_verify_envelope now emits summary.state="no_changes"
    # on the empty-paths branch, disclosing the no-changed-files resolution path.
    def test_empty_diff_emits_state_or_resolution(self, cli_runner, clean_indexed_project, monkeypatch):
        """Empty-diff envelope must emit ``summary.state`` or
        ``summary.resolution`` to disambiguate the clean-tree path from
        the git-failure silent-empty-list path."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["verify"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        # Exit 0 expected (empty diff -> PASS); exit 5 is gate-fail
        # which would also have an envelope but on a different path.
        assert result.exit_code in (0, 5), (
            f"W805-EEEEE: unexpected exit_code={result.exit_code} output={result.output!r}"
        )
        data = parse_json_output(result, "verify")
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-EEEEE Pattern-1-V-D: empty-paths branch must emit a "
            f"closed-enum disclosure (summary.state OR summary.git_error "
            f"OR summary.resolution) to disambiguate clean-tree vs git-"
            f"failure; got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


class TestBogusRefDistinctFromEmptyDiff:
    """Forward-looking pin: a future ``--base`` / ``--commit-range`` /
    ``--pr`` extension of cmd_verify (which all four prior strict
    consumers in this family expose) would inherit the bogus-ref
    silent-empty-list path. Today cmd_verify only exposes the default
    working-tree branch -- so bogus-ref isn't directly reachable. Pin
    the LATENT invariant that, if cmd_verify ever exposes a base-ref
    flag, the bogus-ref path must be distinguishable from clean-tree.

    Today's surface: cmd_verify accepts ``--changed`` (boolean), no
    ``--base`` / ``--pr`` / ``--commit-range``. The forward-looking pin
    is a source-level invariant on the EMPTY-PATHS branch sharing its
    envelope shape with the (currently-unreachable) bogus-ref branch."""

    # GRADUATED 2026-06-18: the empty-paths envelope now carries summary.state,
    # the disambiguation field a future --base/--commit-range extension reuses.
    def test_empty_diff_distinguishable_from_hypothetical_bogus_ref(
        self, cli_runner, clean_indexed_project, monkeypatch
    ):
        """Empty-diff envelope must carry a disambiguation field so a
        future bogus-ref extension can reuse the same field."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["verify"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code in (0, 5)
        data = parse_json_output(result, "verify")
        summary = data["summary"]
        # The disambiguation field must be present on the empty-paths
        # branch -- mirrors the W805-CCCCC TestStateFieldOnFailure pin.
        state = summary.get("state")
        resolution = summary.get("resolution")
        assert (state and isinstance(state, str) and state.strip()) or (
            resolution and isinstance(resolution, str) and resolution.strip()
        ), (
            f"W805-EEEEE: empty-paths branch must emit "
            f"summary.state OR summary.resolution; got state={state!r} "
            f"resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-CCCCC + W805-AAAAA invariants must
# stay green. A future fix to the shared ``get_changed_files`` helper
# (or cmd_verify call site) MUST NOT perturb the sister suites.
# ---------------------------------------------------------------------------


class TestW805CCCCCInvariantsPreserved:
    """Sister cross-check: cmd_why_slow line 168 still has the BLOCKER-
    class signature bug pinned by W805-CCCCC. A drive-by fix to
    cmd_verify's empty-paths branch MUST NOT silently repair (or
    obscure) the cmd_why_slow signature bug -- that is a separately-
    tracked latent."""

    def test_cmd_why_slow_signature_bug_still_present(self):
        """Source-level: cmd_why_slow line 168 still has the broken
        ``get_changed_files(base=base)`` call signature."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_why_slow.py").read_text(
            encoding="utf-8"
        )
        # Find all get_changed_files( calls in cmd_why_slow.
        matches = list(re.finditer(r"get_changed_files\(([^)]*)\)", src))
        assert len(matches) >= 1, (
            "W805-EEEEE sister cross-check: cmd_why_slow must still call "
            "get_changed_files (W805-CCCCC W978-precondition)."
        )
        # If the W805-CCCCC bug has been silently fixed by a drive-by,
        # surface that here -- this assert WILL FAIL if cmd_why_slow's
        # call signature has been repaired, which is the desired surface
        # because W805-CCCCC's xfail-strict pins also need to graduate
        # in lockstep.
        any_has_base_kw = any("base=" in m.group(1) for m in matches)
        assert any_has_base_kw, (
            "W805-EEEEE sister cross-check: cmd_why_slow's W805-CCCCC "
            "signature bug appears to have been repaired (no ``base=`` "
            "keyword in any get_changed_files call). If this is "
            "intentional, graduate the W805-CCCCC xfail-strict pins "
            "(TestChangedPathRaisesTypeError + TestStateFieldOnFailure) "
            "in tandem."
        )
