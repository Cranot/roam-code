"""W805-HHHHH -- shared-helper silent-SAFE probe on ``roam syntax-check``.

Hundred-and-twelfth-in-batch W805 sweep. TENTH potential strict consumer
for the shared-helper resolution-disclosure family on the
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
  * W805-AAAA  (cmd_delete_check)    -- independent ``_git_diff`` helper.
  * W805-CCCCC (cmd_why_slow)        -- LATENT (BLOCKER-class TypeError pre-empts).

Family stood 9-STRONG STRUCTURAL + 1 latent (cmd_why_slow blocked on
TypeError) + 1 independent (cmd_delete_check) at the start of this probe.

W978 first-hypothesis verification
----------------------------------

Source audit of ``src/roam/commands/cmd_syntax_check.py`` head-to-tail:

  * Line 33: ``from roam.commands.changed_files import get_changed_files``.
    The import IS the shared helper used by all nine prior strict
    consumers.
  * Lines 159-164: a private ``_get_changed_files()`` wrapper with TWO
    call sites:

        def _get_changed_files() -> list[str]:
            root = find_project_root()
            unstaged = get_changed_files(root, untracked=True)
            staged = get_changed_files(root, staged=True)
            return sorted(set(unstaged) | set(staged))

    Helper signature (changed_files.py:98-105) accepts ``root``
    positional + ``staged`` / ``untracked`` keywords. Both call sites
    pass ``root`` positionally + a valid keyword. NO TypeError-class
    bug (unlike W805-CCCCC's cmd_why_slow). W978 CONFIRM for the
    strict-consumer family AND for signature correctness.
  * Lines 213-220: when ``--changed`` is set, ``file_list =
    _get_changed_files()``. Returncode != 0 in either underlying ``git
    diff`` call silently returns ``[]`` -- the union is also ``[]`` --
    and execution falls through to the empty-files branch (lines
    225-244).
  * Lines 225-244: empty-files branch emits:
      * ``verdict: "No files to check"``
      * ``total_files: 0``
      * ``files_with_errors: 0``
      * ``total_errors: 0``
      * ``clean: True``
    ZERO closed-enum disclosure of the empty-diff resolution. The
    summary is INDISTINGUISHABLE between:
      1. Clean working tree (no changes) -- legitimately "No files".
      2. Git diff failed (returncode != 0 on either unstaged or staged)
         -- shared helper returns ``[]``, union still ``[]``, command
         still emits ``clean: True``.
      3. Git not available (FileNotFoundError) -- same shared-helper
         silent-empty-list path, same ``clean: True`` verdict.

This is the canonical Pattern-1-Variant-D silent-SAFE family-member
shape: success verdict indistinguishable from degraded resolution.
REAL BUG #1 (silent-SAFE family member) pinned strict.

Distinguishing axis from prior 9: TWO call sites unioned via
``set(unstaged) | set(staged)``. A failure of EITHER underlying call
silently degrades the union -- a stricter variant than the single-call
shape on cmd_verify/cmd_boundary/etc. The W805-VVVV cmd_affected probe
also had two call sites, but those flowed through separate envelope
paths; here both feed into one ``file_list`` and one verdict.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_syntax_check.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): only the deferred ``from
tree_sitter_language_pack import get_parser`` at line 140 and ``from
roam.exit_codes import EXIT_GATE_FAILURE`` at line 298. Both are bare
lazy-imports for legitimate hot-path / except-block reasons (heavy
grammar pack on first parse; exit-code constant only needed on the
gate-failure terminal). No false-cycle hedging language. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 9-STRONG STRUCTURAL + 1 latent + 1 independent.

After this probe: family **10-STRONG STRUCTURAL** (EEEE / JJJJ / OOOO /
RRRR / SSSS / VVVV / XXXX / AAAAA / EEEEE / **HHHHH**) + 1 latent +
1 independent = 12 family members total. cmd_syntax_check is the TENTH
structural strict consumer.

W805 sweep update
-----------------

W805 sweep yield 56/56 (this probe = 56th). Strict-consumer family is
now 10-STRONG fully structural. A single fix to ``get_changed_files``
(returning ``(paths, error_kind)``) atomically unblocks TEN consumers.

Next W805 sweep candidate (W805-IIIII)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-EEEEE
canonical-list ordering: cmd_suggest_reviewers, cmd_coupling, cmd_plan.
W805-IIIII candidate: cmd_suggest_reviewers -- next-most-likely
shared-helper consumer per the W805-EEEEE strict-consumer ordering.
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
)

from roam.cli import cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def clean_git_project(tmp_path):
    """Git-initialised project with a clean working tree.

    A clean working tree means BOTH ``get_changed_files(root,
    untracked=True)`` AND ``get_changed_files(root, staged=True)``
    return ``[]`` -- exercising the empty-files branch of
    cmd_syntax_check (lines 225-244). That branch emits the silent-SAFE
    ``clean: True`` envelope this probe pins. cmd_syntax_check does NOT
    require an index, so we skip the indexing step.
    """
    proj = tmp_path / "clean-syntax-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def greet(name):\n    return f'hi {name}'\n")
    git_init(proj)
    return proj


def _invoke_syntax_check(proj, *args, json_mode=False):
    """Run ``roam syntax-check`` in-process via CliRunner, cwd=proj.

    Mirrors the canonical ``_invoke`` helper in test_syntax_check.py so
    the W805-HHHHH probe exercises the same invocation surface as the
    base regression suite.
    """
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.append("syntax-check")
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
# W978 verification -- cmd_syntax_check actually consumes
# ``get_changed_files``. If cmd_syntax_check is refactored to a
# different helper, this test surfaces the structural drift before the
# W805-HHHHH pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdSyntaxCheckConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_syntax_check imports +
    calls ``get_changed_files``. Source-level invariant elevating
    W805-HHHHH from a coincidental shape match to a structural shared-
    helper audit."""

    def test_cmd_syntax_check_consumes_get_changed_files(self):
        """Source-level check: cmd_syntax_check imports + calls
        get_changed_files."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_syntax_check.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-HHHHH W978-precondition: cmd_syntax_check must import "
            "from roam.commands.changed_files; if this changed, re-audit "
            "the shared-helper family membership."
        )
        assert "get_changed_files(" in src, (
            "W805-HHHHH W978-precondition: cmd_syntax_check must call "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )

    def test_cmd_syntax_check_has_two_call_sites(self):
        """Source-level: cmd_syntax_check calls the shared helper
        ``get_changed_files`` TWICE (one for unstaged+untracked, one
        for staged), unioning the results. The two-call-site shape
        distinguishes W805-HHHHH from the single-call shape on
        cmd_verify / cmd_boundary / etc. Filter out the private
        ``_get_changed_files`` wrapper's own def + invocation so only
        the SHARED-HELPER call sites count."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_syntax_check.py").read_text(
            encoding="utf-8"
        )
        # Match only the shared-helper calls (pass ``root`` as first
        # positional). The private wrapper takes no args; this regex
        # excludes both its def-site and call-site.
        matches = list(re.finditer(r"(?<!_)get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 2, (
            f"W805-HHHHH: cmd_syntax_check should have exactly two "
            f"SHARED-helper get_changed_files(root, ...) call sites "
            f"(unstaged + staged); got {len(matches)}. If a refactor "
            f"consolidated to one call, re-audit the shared-helper "
            f"family shape."
        )


# ---------------------------------------------------------------------------
# W805-CCCCC-echo BLOCKER probe -- the ``roam syntax-check --changed``
# invocation must NOT raise TypeError. W805-CCCCC found a cmd_why_slow:168
# signature bug (``get_changed_files(base=base)`` -- both positional
# ``root`` missing AND ``base`` not an accepted keyword). Probe whether
# cmd_syntax_check has the same class of upstream bug. The source audit
# says NO -- lines 162-163 are ``get_changed_files(root, untracked=True)``
# and ``get_changed_files(root, staged=True)`` which are correct -- but
# pin the invariant so future refactors that introduce a similar
# TypeError-class signature break get caught immediately.
# ---------------------------------------------------------------------------


class TestChangedModeNoSignatureTypeError:
    """``roam syntax-check --changed`` invocation (which calls
    ``_get_changed_files()`` -> two ``get_changed_files(root, ...)``
    calls) must not raise TypeError. This is the W805-CCCCC-echo
    probe -- confirms cmd_syntax_check is NOT BLOCKER-class like
    cmd_why_slow."""

    def test_syntax_check_changed_no_signature_typeerror(self, cli_runner, clean_git_project, monkeypatch):
        """``roam syntax-check --changed`` must exit cleanly (not raise
        TypeError)."""
        monkeypatch.chdir(clean_git_project)
        result = _invoke_syntax_check(clean_git_project, "--changed", json_mode=True)
        # Must not be a TypeError-class crash. Exit code 0 (clean) or 5
        # (gate failure) are both acceptable -- both prove the call
        # signature is correct. Anything else (especially TypeError
        # exception) is the BLOCKER class.
        assert result.exit_code in (0, 5), (
            f"W805-HHHHH BLOCKER probe (W805-CCCCC echo): "
            f"``roam syntax-check --changed`` must exit cleanly with 0 "
            f"or 5, not raise TypeError; got exit_code="
            f"{result.exit_code} output={result.output!r}"
        )

    def test_syntax_check_call_sites_use_correct_signature(self):
        """Source-level: both shared-helper ``get_changed_files(root,
        ...)`` call sites must use the correct signature -- ``root``
        positionally, no ``base=`` keyword pattern from W805-CCCCC.
        Excludes the private ``_get_changed_files`` wrapper (which is
        a no-arg internal helper)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_syntax_check.py").read_text(
            encoding="utf-8"
        )
        matches = list(re.finditer(r"(?<!_)get_changed_files\(\s*root\b([^)]*)\)", src))
        assert len(matches) == 2, (
            f"W805-HHHHH: cmd_syntax_check must have exactly two "
            f"shared-helper get_changed_files(root, ...) call sites; "
            f"got {len(matches)}"
        )
        # W805-CCCCC bug pattern: ``base=`` keyword (helper has no such
        # kwarg). Helper accepts ``base_ref=`` instead.
        for m in matches:
            call_args = m.group(1).strip()
            assert "base=" not in call_args, (
                f"W805-HHHHH W805-CCCCC echo: cmd_syntax_check must NOT "
                f"use the ``base=`` keyword (helper signature uses "
                f"``base_ref=``); got call args: {call_args!r}"
            )


# ---------------------------------------------------------------------------
# REAL BUG #1: silent-SAFE family member.
#
# cmd_syntax_check's empty-files branch (lines 225-244) emits
# ``verdict: "No files to check", clean: True`` with no closed-enum
# disclosure of the empty-diff resolution. The summary is
# indistinguishable between (a) clean working tree and (b) shared-helper
# silent-empty-list on git failure of either underlying call. Pinned
# strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The empty-files branch (lines 225-244) inherits the silent-SAFE
    family shape from the shared helper. Today the summary carries only
    ``verdict / total_files / files_with_errors / total_errors /
    clean`` -- no closed-enum ``state`` / ``git_error`` / ``resolution``
    field disclosing the no-changes-vs-git-failure disambiguation.
    Pattern-1-Variant-D requires the no-changes path to be
    distinguishable. Pinned strict; graduates when a closed-enum
    disclosure is added."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-HHHHH REAL BUG #1 (silent-SAFE family member, "
            "10th structural consumer): "
            "src/roam/commands/cmd_syntax_check.py:225-244 emits "
            "``verdict: 'No files to check', clean: True`` with no "
            "closed-enum state / git_error / resolution field on the "
            "empty-files branch. The summary is indistinguishable "
            "between (a) clean working tree and (b) shared-helper "
            "silent-empty-list on git failure of EITHER the unstaged or "
            "staged underlying call. Pattern-1-Variant-D requires the "
            "no-changes path to disclose the resolution state. Pinned "
            "strict; graduates when ``summary.state`` (e.g. "
            "``no_changes`` / ``git_unavailable`` / ``empty_diff``) or "
            "``summary.resolution`` is added on the empty-files branch."
        ),
    )
    def test_empty_diff_emits_state_or_resolution(self, cli_runner, clean_git_project, monkeypatch):
        """Empty-diff envelope must emit ``summary.state`` or
        ``summary.resolution`` to disambiguate the clean-tree path from
        the git-failure silent-empty-list path."""
        monkeypatch.chdir(clean_git_project)
        result = _invoke_syntax_check(clean_git_project, "--changed", json_mode=True)
        # Exit 0 expected (empty diff -> clean=True).
        assert result.exit_code == 0, f"W805-HHHHH: unexpected exit_code={result.exit_code} output={result.output!r}"
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
            f"W805-HHHHH Pattern-1-V-D: empty-files branch must emit a "
            f"closed-enum disclosure (summary.state OR summary.git_error "
            f"OR summary.resolution) to disambiguate clean-tree vs git-"
            f"failure; got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


class TestBogusRefDistinctFromEmptyDiff:
    """Forward-looking pin: cmd_syntax_check exposes only ``--changed``
    today (no ``--base`` / ``--commit-range`` / ``--pr`` extension).
    The bogus-ref path isn't directly reachable -- but the silent-SAFE
    envelope shape is shared with any future base-ref extension. Pin
    the LATENT invariant that the empty-files branch carries a
    disambiguation field so a future bogus-ref extension can reuse the
    same field."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-HHHHH REAL BUG #1 ECHO (forward-looking): "
            "cmd_syntax_check's empty-files branch (lines 225-244) "
            "emits the same envelope shape that a future ``--base`` / "
            "``--commit-range`` extension would exercise on a bogus-"
            "ref. Today the surface only exposes ``--changed`` "
            "(working-tree + staged + untracked), but the silent-SAFE "
            "shape is shared. Once a state / resolution field is added "
            "(graduating REAL BUG #1), the same field disambiguates "
            "bogus-ref future-extensions. Pinned as a strict invariant "
            "on the empty-files envelope shape."
        ),
    )
    def test_empty_diff_distinguishable_from_hypothetical_bogus_ref(self, cli_runner, clean_git_project, monkeypatch):
        """Empty-diff envelope must carry a disambiguation field so a
        future bogus-ref extension can reuse the same field."""
        monkeypatch.chdir(clean_git_project)
        result = _invoke_syntax_check(clean_git_project, "--changed", json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        summary = data["summary"]
        # The disambiguation field must be present on the empty-files
        # branch -- mirrors the W805-EEEEE TestStateFieldOnFailure pin.
        state = summary.get("state")
        resolution = summary.get("resolution")
        assert (state and isinstance(state, str) and state.strip()) or (
            resolution and isinstance(resolution, str) and resolution.strip()
        ), (
            f"W805-HHHHH: empty-files branch must emit "
            f"summary.state OR summary.resolution; got state={state!r} "
            f"resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-EEEEE + W805-CCCCC invariants must
# stay green. A future fix to the shared ``get_changed_files`` helper
# (or cmd_syntax_check call site) MUST NOT perturb the sister suites.
# ---------------------------------------------------------------------------


class TestW805EEEEEInvariantsPreserved:
    """Sister cross-check: cmd_verify's empty-paths branch (lines
    708-729) still emits the silent-SAFE ``verdict: 'PASS'`` envelope
    pinned by W805-EEEEE. A drive-by fix to cmd_syntax_check's empty-
    files branch MUST NOT silently repair (or obscure) the cmd_verify
    silent-SAFE shape -- that is a separately-tracked sister pin."""

    def test_cmd_verify_still_consumes_shared_helper(self):
        """Source-level: verify retains failure-aware status discovery."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_verify.py").read_text(
            encoding="utf-8"
        )
        assert "def _discover_verify_targets(root: Path) -> dict:" in src
        assert "_discover_verify_targets(root)" in src

    def test_cmd_why_slow_signature_bug_still_present(self):
        """Source-level: cmd_why_slow still has the broken ``base=``
        keyword pattern from W805-CCCCC. If silently repaired, surface
        here so the W805-CCCCC xfail-strict pins graduate in lockstep."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_why_slow.py").read_text(
            encoding="utf-8"
        )
        matches = list(re.finditer(r"get_changed_files\(([^)]*)\)", src))
        assert len(matches) >= 1, (
            "W805-HHHHH sister cross-check: cmd_why_slow must still "
            "call get_changed_files (W805-CCCCC W978-precondition)."
        )
        any_has_base_kw = any("base=" in m.group(1) for m in matches)
        assert any_has_base_kw, (
            "W805-HHHHH sister cross-check: cmd_why_slow's W805-CCCCC "
            "signature bug appears to have been repaired (no ``base=`` "
            "keyword in any get_changed_files call). If this is "
            "intentional, graduate the W805-CCCCC xfail-strict pins in "
            "tandem."
        )
