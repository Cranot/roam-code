"""W805-JJJJJ -- shared-helper silent-SAFE probe on ``roam suggest-reviewers``.

Hundred-and-fourteenth-in-batch W805 sweep. ELEVENTH potential strict
consumer for the shared-helper resolution-disclosure family on the
``get_changed_files`` axis.

Family lineage entering this probe:

  * W805-EEEE  (cmd_diff)            -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ  (cmd_pr_diff)         -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO  (cmd_attest)          -- THIRD strict consumer.
  * W805-RRRR  (cmd_test_gaps)       -- FOURTH strict consumer.
  * W805-SSSS  (cmd_affected_tests)  -- FIFTH (STRICTLY WORST -- plain text in --json mode).
  * W805-VVVV  (cmd_affected)        -- SIXTH (envelope shape, two call sites).
  * W805-XXXX  (cmd_adversarial)     -- SEVENTH (envelope shape).
  * W805-AAAAA (cmd_boundary)        -- EIGHTH (envelope shape).
  * W805-EEEEE (cmd_verify)          -- NINTH (envelope shape).
  * W805-HHHHH (cmd_syntax_check)    -- TENTH (two call sites unioned).
  * W805-AAAA  (cmd_delete_check)    -- independent ``_git_diff`` helper.
  * W805-CCCCC (cmd_why_slow)        -- LATENT (BLOCKER-class TypeError pre-empts).

Family stood 10-STRONG STRUCTURAL + 1 latent (cmd_why_slow blocked on
TypeError) + 1 independent (cmd_delete_check) at the start of this probe.

W978 first-hypothesis verification
----------------------------------

Source audit of ``src/roam/commands/cmd_suggest_reviewers.py`` head-to-tail:

  * Line 24: ``from roam.commands.changed_files import get_changed_files,
    resolve_changed_to_db``. The import IS the shared helper used by all
    ten prior strict consumers + the resolve helper.
  * Lines 330-336: TWO call sites of ``get_changed_files(root)`` --
    one when ``--changed`` is set (line 331), one on the default branch
    (line 336). Both pass ``root`` positionally only. Helper signature
    (changed_files.py:98-105) accepts ``root`` positional + keyword
    defaults. NO TypeError-class bug (unlike W805-CCCCC's cmd_why_slow).
    W978 CONFIRM for strict-consumer family AND signature correctness.
  * Lines 338-353: when ``changed`` is empty, the empty-files branch
    emits:
      * ``verdict: "No changed files found"``
      * ``reviewers: []``
      * ``coverage: {"covered": 0, "total": 0, "uncovered_files": []}``
      * ``changed_files: []``
    ZERO closed-enum disclosure of the empty-diff resolution. The summary
    is INDISTINGUISHABLE between:
      1. Clean working tree (no changes) -- legitimately "No changed files".
      2. Git diff failed (returncode != 0) -- shared helper returns ``[]``,
         command still emits the same "No changed files found" verdict.
      3. Git not available (FileNotFoundError) -- same shared-helper
         silent-empty-list path, same verdict.
  * Lines 355-373: there is a SECOND empty-resolution branch when
    ``file_map`` is empty (changed files present but none in index).
    This branch emits ``verdict: "Changed files not in index"`` -- it
    DOES distinguish "files not in index" from "no changed files",
    but the upstream empty-diff path still doesn't disambiguate
    clean-tree vs git-failure.

This is the canonical Pattern-1-Variant-D silent-SAFE family-member
shape: success verdict indistinguishable from degraded resolution.
REAL BUG #1 (silent-SAFE family member) pinned strict.

Distinguishing axis from prior 10: TWO call sites on DIFFERENT code paths
(``--changed`` flag-controlled vs default-branch). cmd_syntax_check
(W805-HHHHH) had two call sites unioned via ``set()`` operations;
cmd_suggest_reviewers has them on mutually exclusive branches. Either
path silently degrades to ``[]`` and falls through to the same empty-
files envelope.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_suggest_reviewers.py for the W907 patterns (``avoid.*cycle``
/ ``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): no matches. All imports are top-of-
file. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 10-STRONG STRUCTURAL + 1 latent + 1 independent.

After this probe: family **11-STRONG STRUCTURAL** (EEEE / JJJJ / OOOO /
RRRR / SSSS / VVVV / XXXX / AAAAA / EEEEE / HHHHH / **JJJJJ**) + 1
latent + 1 independent = 13 family members total. cmd_suggest_reviewers
is the ELEVENTH structural strict consumer.

W805 sweep update
-----------------

W805 sweep yield 57/57 (this probe = 57th). Strict-consumer family is
now 11-STRONG fully structural. A single fix to ``get_changed_files``
(returning ``(paths, error_kind)``) atomically unblocks ELEVEN consumers.

Next W805 sweep candidate (W805-KKKKK)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-EEEEE
canonical-list ordering: cmd_coupling, cmd_plan. W805-KKKKK candidate:
cmd_coupling -- next-most-likely shared-helper consumer per the W805-
EEEEE strict-consumer ordering.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
)

from roam.cli import cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def clean_indexed_project(tmp_path):
    """Git-initialised + indexed project with a clean working tree.

    A clean working tree means ``get_changed_files(root)`` returns ``[]``
    -- exercising the empty-files branch of cmd_suggest_reviewers
    (lines 338-353). That branch emits the silent-SAFE
    ``verdict: "No changed files found"`` envelope this probe pins.
    cmd_suggest_reviewers requires an index (``ensure_index()`` at
    line 326), so we run ``roam index`` after git init.
    """
    proj = tmp_path / "clean-reviewers-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def greet(name):\n    return f'hi {name}'\n")
    git_init(proj)
    # Index so ensure_index() doesn't bail.
    index_in_process(proj)
    return proj


def _invoke_suggest_reviewers(proj, *args, json_mode=False):
    """Run ``roam suggest-reviewers`` in-process via CliRunner, cwd=proj.

    Mirrors the canonical invocation pattern used by the W805 sister
    suites so the W805-JJJJJ probe exercises the same surface as the
    base regression suite.
    """
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.append("suggest-reviewers")
    full_args.extend(args)

    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


# ---------------------------------------------------------------------------
# W978 verification -- cmd_suggest_reviewers actually consumes
# ``get_changed_files``. If cmd_suggest_reviewers is refactored to a
# different helper, this test surfaces the structural drift before the
# W805-JJJJJ pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdSuggestReviewersConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_suggest_reviewers imports +
    calls ``get_changed_files``. Source-level invariant elevating
    W805-JJJJJ from a coincidental shape match to a structural shared-
    helper audit."""

    def test_cmd_suggest_reviewers_consumes_get_changed_files(self):
        """Source-level check: cmd_suggest_reviewers imports + calls
        get_changed_files."""
        src = (
            Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_suggest_reviewers.py"
        ).read_text(encoding="utf-8")
        assert "from roam.commands.changed_files import" in src, (
            "W805-JJJJJ W978-precondition: cmd_suggest_reviewers must "
            "import from roam.commands.changed_files; if this changed, "
            "re-audit the shared-helper family membership."
        )
        assert "get_changed_files(" in src, (
            "W805-JJJJJ W978-precondition: cmd_suggest_reviewers must "
            "call get_changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )

    def test_cmd_suggest_reviewers_has_two_call_sites(self):
        """Source-level: cmd_suggest_reviewers calls the shared helper
        ``get_changed_files`` TWICE (one for ``--changed`` flag, one for
        default-branch unstaged). Distinguishing axis from W805-HHHHH:
        these are on MUTUALLY EXCLUSIVE branches (if/else), not unioned."""
        src = (
            Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_suggest_reviewers.py"
        ).read_text(encoding="utf-8")
        matches = list(re.finditer(r"get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 2, (
            f"W805-JJJJJ: cmd_suggest_reviewers should have exactly two "
            f"shared-helper get_changed_files(root, ...) call sites "
            f"(--changed branch + default-branch); got {len(matches)}. "
            f"If a refactor consolidated to one call, re-audit the "
            f"shared-helper family shape."
        )


# ---------------------------------------------------------------------------
# W805-CCCCC-echo BLOCKER probe -- the ``roam suggest-reviewers --changed``
# invocation must NOT raise TypeError. W805-CCCCC found a cmd_why_slow:168
# signature bug (``get_changed_files(base=base)`` -- both positional
# ``root`` missing AND ``base`` not an accepted keyword). Probe whether
# cmd_suggest_reviewers has the same class of upstream bug. The source
# audit says NO -- lines 331 and 336 are both ``get_changed_files(root)``
# which is correct -- but pin the invariant so future refactors that
# introduce a similar TypeError-class signature break get caught
# immediately.
# ---------------------------------------------------------------------------


class TestChangedModeNoSignatureTypeError:
    """``roam suggest-reviewers --changed`` invocation (which calls
    ``get_changed_files(root)``) must not raise TypeError. This is the
    W805-CCCCC-echo probe -- confirms cmd_suggest_reviewers is NOT
    BLOCKER-class like cmd_why_slow."""

    def test_suggest_reviewers_changed_no_signature_typeerror(self, cli_runner, clean_indexed_project, monkeypatch):
        """``roam suggest-reviewers --changed`` must exit cleanly (not
        raise TypeError)."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_suggest_reviewers(clean_indexed_project, "--changed", json_mode=True)
        # Must not be a TypeError-class crash. Exit code 0 (clean) or 5
        # (gate failure) are both acceptable -- both prove the call
        # signature is correct. Anything else (especially TypeError
        # exception) is the BLOCKER class.
        assert result.exit_code in (0, 5), (
            f"W805-JJJJJ BLOCKER probe (W805-CCCCC echo): "
            f"``roam suggest-reviewers --changed`` must exit cleanly with "
            f"0 or 5, not raise TypeError; got exit_code="
            f"{result.exit_code} output={result.output!r}"
        )

    def test_suggest_reviewers_call_sites_use_correct_signature(self):
        """Source-level: both shared-helper ``get_changed_files(root)``
        call sites must use the correct signature -- ``root`` positionally,
        no ``base=`` keyword pattern from W805-CCCCC."""
        src = (
            Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_suggest_reviewers.py"
        ).read_text(encoding="utf-8")
        matches = list(re.finditer(r"get_changed_files\(\s*root\b([^)]*)\)", src))
        assert len(matches) == 2, (
            f"W805-JJJJJ: cmd_suggest_reviewers must have exactly two "
            f"shared-helper get_changed_files(root, ...) call sites; "
            f"got {len(matches)}"
        )
        # W805-CCCCC bug pattern: ``base=`` keyword (helper has no such
        # kwarg). Helper accepts ``base_ref=`` instead.
        for m in matches:
            call_args = m.group(1).strip()
            assert "base=" not in call_args, (
                f"W805-JJJJJ W805-CCCCC echo: cmd_suggest_reviewers must "
                f"NOT use the ``base=`` keyword (helper signature uses "
                f"``base_ref=``); got call args: {call_args!r}"
            )


# ---------------------------------------------------------------------------
# REAL BUG #1: silent-SAFE family member.
#
# cmd_suggest_reviewers's empty-files branch (lines 338-353) emits
# ``verdict: "No changed files found"`` with no closed-enum disclosure
# of the empty-diff resolution. The summary is indistinguishable between
# (a) clean working tree and (b) shared-helper silent-empty-list on git
# failure. Pinned strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The empty-files branch (lines 338-353) inherits the silent-SAFE
    family shape from the shared helper. Today the summary carries only
    ``verdict`` -- no closed-enum ``state`` / ``git_error`` /
    ``resolution`` field disclosing the no-changes-vs-git-failure
    disambiguation. Pattern-1-Variant-D requires the no-changes path to
    be distinguishable. Pinned strict; graduates when a closed-enum
    disclosure is added."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJJ REAL BUG #1 (silent-SAFE family member, "
            "11th structural consumer): "
            "src/roam/commands/cmd_suggest_reviewers.py:338-353 emits "
            "``verdict: 'No changed files found'`` with no closed-enum "
            "state / git_error / resolution field on the empty-files "
            "branch. The summary is indistinguishable between (a) clean "
            "working tree and (b) shared-helper silent-empty-list on git "
            "failure (returncode != 0 OR FileNotFoundError). "
            "Pattern-1-Variant-D requires the no-changes path to disclose "
            "the resolution state. Pinned strict; graduates when "
            "``summary.state`` (e.g. ``no_changes`` / ``git_unavailable`` "
            "/ ``empty_diff``) or ``summary.resolution`` is added on the "
            "empty-files branch."
        ),
    )
    def test_empty_diff_emits_state_or_resolution(self, cli_runner, clean_indexed_project, monkeypatch):
        """Empty-diff envelope must emit ``summary.state`` or
        ``summary.resolution`` to disambiguate the clean-tree path from
        the git-failure silent-empty-list path."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_suggest_reviewers(clean_indexed_project, "--changed", json_mode=True)
        # Exit 0 expected (empty diff -> no changed files).
        assert result.exit_code == 0, f"W805-JJJJJ: unexpected exit_code={result.exit_code} output={result.output!r}"
        data = json.loads(result.output)
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-JJJJJ Pattern-1-V-D: empty-files branch must emit a "
            f"closed-enum disclosure (summary.state OR summary.git_error "
            f"OR summary.resolution) to disambiguate clean-tree vs git-"
            f"failure; got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


class TestBogusRefDistinctFromEmptyDiff:
    """Forward-looking pin: cmd_suggest_reviewers exposes only ``--changed``
    today (no ``--base`` / ``--commit-range`` / ``--pr`` extension). The
    bogus-ref path isn't directly reachable -- but the silent-SAFE
    envelope shape is shared with any future base-ref extension. Pin the
    LATENT invariant that the empty-files branch carries a disambiguation
    field so a future bogus-ref extension can reuse the same field."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJJJJ REAL BUG #1 ECHO (forward-looking): "
            "cmd_suggest_reviewers's empty-files branch (lines 338-353) "
            "emits the same envelope shape that a future ``--base`` / "
            "``--commit-range`` extension would exercise on a bogus-"
            "ref. Today the surface only exposes ``--changed`` "
            "(working-tree unstaged), but the silent-SAFE shape is "
            "shared. Once a state / resolution field is added "
            "(graduating REAL BUG #1), the same field disambiguates "
            "bogus-ref future-extensions. Pinned as a strict invariant "
            "on the empty-files envelope shape."
        ),
    )
    def test_empty_diff_distinguishable_from_hypothetical_bogus_ref(
        self, cli_runner, clean_indexed_project, monkeypatch
    ):
        """Empty-diff envelope must carry a disambiguation field so a
        future bogus-ref extension can reuse the same field."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_suggest_reviewers(clean_indexed_project, "--changed", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        summary = data["summary"]
        # The disambiguation field must be present on the empty-files
        # branch -- mirrors the W805-HHHHH TestStateFieldOnFailure pin.
        state = summary.get("state")
        resolution = summary.get("resolution")
        assert (state and isinstance(state, str) and state.strip()) or (
            resolution and isinstance(resolution, str) and resolution.strip()
        ), (
            f"W805-JJJJJ: empty-files branch must emit "
            f"summary.state OR summary.resolution; got state={state!r} "
            f"resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-HHHHH + W805-EEEEE + W805-CCCCC
# invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper (or cmd_suggest_reviewers call site) MUST
# NOT perturb the sister suites.
# ---------------------------------------------------------------------------


class TestW805HHHHHInvariantsPreserved:
    """Sister cross-check: cmd_syntax_check's empty-files branch (lines
    225-244) still emits the silent-SAFE ``verdict: 'No files to check'``
    envelope pinned by W805-HHHHH. A drive-by fix to cmd_suggest_reviewers
    MUST NOT silently repair (or obscure) the cmd_syntax_check silent-
    SAFE shape -- that is a separately-tracked sister pin."""

    def test_cmd_syntax_check_still_consumes_shared_helper(self):
        """Source-level: cmd_syntax_check still calls
        ``get_changed_files(root, ...)``."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_syntax_check.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-JJJJJ sister cross-check: cmd_syntax_check must still "
            "import from roam.commands.changed_files (W805-HHHHH W978-"
            "precondition)."
        )
        # cmd_syntax_check has two shared-helper call sites (unstaged +
        # staged), excluding its private wrapper.
        matches = list(re.finditer(r"(?<!_)get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 2, (
            f"W805-JJJJJ sister cross-check: cmd_syntax_check must "
            f"still have two shared-helper get_changed_files(root, ...) "
            f"call sites (W805-HHHHH W978-precondition); got "
            f"{len(matches)}"
        )

    def test_cmd_verify_still_consumes_shared_helper(self):
        """Source-level: cmd_verify line 706 still calls
        ``get_changed_files(root)`` (W805-EEEEE W978-precondition)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_verify.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-JJJJJ sister cross-check: cmd_verify must still import "
            "from roam.commands.changed_files (W805-EEEEE W978-"
            "precondition)."
        )
        assert "get_changed_files(root)" in src, (
            "W805-JJJJJ sister cross-check: cmd_verify must still call "
            "get_changed_files(root) on the default-branch; if "
            "refactored, graduate the W805-EEEEE xfail-strict pins."
        )

    def test_cmd_why_slow_signature_bug_still_present(self):
        """Source-level: cmd_why_slow still has the broken ``base=``
        keyword pattern from W805-CCCCC. If silently repaired, surface
        here so the W805-CCCCC xfail-strict pins graduate in lockstep."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_why_slow.py").read_text(
            encoding="utf-8"
        )
        matches = list(re.finditer(r"get_changed_files\(([^)]*)\)", src))
        assert len(matches) >= 1, (
            "W805-JJJJJ sister cross-check: cmd_why_slow must still "
            "call get_changed_files (W805-CCCCC W978-precondition)."
        )
        any_has_base_kw = any("base=" in m.group(1) for m in matches)
        assert any_has_base_kw, (
            "W805-JJJJJ sister cross-check: cmd_why_slow's W805-CCCCC "
            "signature bug appears to have been repaired (no ``base=`` "
            "keyword in any get_changed_files call). If this is "
            "intentional, graduate the W805-CCCCC xfail-strict pins in "
            "tandem."
        )
