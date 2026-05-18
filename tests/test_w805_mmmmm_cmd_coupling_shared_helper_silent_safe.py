"""W805-MMMMM -- shared-helper silent-SAFE probe on ``roam coupling``.

Hundred-and-seventeenth-in-batch W805 sweep. TWELFTH potential strict
consumer for the shared-helper resolution-disclosure family on the
``get_changed_files`` axis.

Family lineage entering this probe:

  * W805-EEEE  (cmd_diff)              -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ  (cmd_pr_diff)           -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO  (cmd_attest)            -- THIRD strict consumer.
  * W805-RRRR  (cmd_test_gaps)         -- FOURTH strict consumer.
  * W805-SSSS  (cmd_affected_tests)    -- FIFTH (STRICTLY WORST -- plain text in --json).
  * W805-VVVV  (cmd_affected)          -- SIXTH (envelope shape, two call sites).
  * W805-XXXX  (cmd_adversarial)       -- SEVENTH (envelope shape).
  * W805-AAAAA (cmd_boundary)          -- EIGHTH (envelope shape).
  * W805-EEEEE (cmd_verify)            -- NINTH (envelope shape).
  * W805-HHHHH (cmd_syntax_check)      -- TENTH (two call sites unioned).
  * W805-JJJJJ (cmd_suggest_reviewers) -- ELEVENTH (two mutually-exclusive call sites).
  * W805-AAAA  (cmd_delete_check)      -- independent ``_git_diff`` helper.
  * W805-CCCCC (cmd_why_slow)          -- LATENT (BLOCKER-class TypeError pre-empts).

Family stood 11-STRONG STRUCTURAL + 1 latent (cmd_why_slow blocked on
TypeError) + 1 independent (cmd_delete_check) at the start of this probe.

W978 first-hypothesis verification
----------------------------------

Source audit of ``src/roam/commands/cmd_coupling.py`` head-to-tail:

  * Line 20: ``from roam.commands.changed_files import get_changed_files,
    resolve_changed_to_db``. The import IS the shared helper used by all
    eleven prior strict consumers + the resolve helper.
  * Line 357: SINGLE call site --
      ``changed = get_changed_files(root, staged=staged, commit_range=commit_range)``
    Helper signature (changed_files.py:98-105) accepts ``root``
    positional + ``staged`` / ``commit_range`` keywords. Call site
    passes ``root`` positionally + both valid keywords. NO TypeError-class
    bug (unlike W805-CCCCC's cmd_why_slow). W978 CONFIRM for the
    strict-consumer family AND for signature correctness.
  * Lines 355-372: when ``staged`` or ``commit_range`` is set, the
    ``--against``/``--staged`` branch runs ``get_changed_files``. If the
    helper returns ``[]`` (either legitimate no-changes, OR returncode
    != 0 silent-fallback, OR FileNotFoundError silent-fallback), the
    branch emits:
      * ``summary: {"error": f"No changes for {label}"}``
    where ``label = commit_range or "staged"``. ZERO closed-enum
    disclosure of the empty-diff resolution. The summary is
    INDISTINGUISHABLE between:
      1. Clean staging area / empty commit range -- legitimately "no changes".
      2. Git diff failed (returncode != 0 on bogus ref) -- shared helper
         returns ``[]``, command emits the same ``error`` text.
      3. Git not available (FileNotFoundError) -- same silent-empty-list
         path, same ``error`` text.

This is the canonical Pattern-1-Variant-D silent-SAFE family-member
shape: success/error verdict indistinguishable from degraded resolution.
REAL BUG #1 (silent-SAFE family member) pinned strict.

Distinguishing axis from prior 11: cmd_coupling supports BOTH
``--staged`` AND ``--against <ref>`` (commit_range). The bogus-ref path
is DIRECTLY REACHABLE today (``roam coupling --against bogus-ref-foo``).
Prior 11 either gated on ``--changed`` flag (cmd_suggest_reviewers /
cmd_syntax_check) or had a single default-branch unstaged path
(cmd_verify, cmd_boundary, cmd_adversarial, cmd_affected). cmd_coupling
exposes BOTH the empty-diff path AND the bogus-ref path through the
same single-call-site silent-empty-list -- the same envelope shape
collapses two semantically distinct resolution states.

Additionally, cmd_coupling's empty-files envelope uses
``summary.error`` rather than ``summary.verdict`` (unlike the prior 11
which all emit ``summary.verdict``). This is itself a LAW-6 violation
(verdict must work standalone) but does NOT block the silent-SAFE
family-shape pin -- both paths share the closed-enum disclosure gap.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_coupling.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` / ``circular`` case-insensitive): no matches. All
imports are top-of-file. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 11-STRONG STRUCTURAL + 1 latent + 1 independent.

After this probe: family **12-STRONG STRUCTURAL** (EEEE / JJJJ / OOOO /
RRRR / SSSS / VVVV / XXXX / AAAAA / EEEEE / HHHHH / JJJJJ /
**MMMMM**) + 1 latent + 1 independent = 14 family members total.
cmd_coupling is the TWELFTH structural strict consumer.

W805 sweep update
-----------------

W805 sweep yield 58/58 (this probe = 58th). Strict-consumer family is
now 12-STRONG fully structural. A single fix to ``get_changed_files``
(returning ``(paths, error_kind)``) atomically unblocks TWELVE consumers.

Next W805 sweep candidate (W805-NNNNN)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-EEEEE
canonical-list ordering: cmd_plan (LAST candidate in the canonical
strict-consumer ordering). W805-NNNNN candidate: cmd_plan --
final shared-helper consumer per the W805-EEEEE strict-consumer
ordering.
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
    """Git-initialised + indexed project with a clean staging area.

    A clean staging area means ``get_changed_files(root, staged=True)``
    returns ``[]`` -- exercising the empty-files branch of cmd_coupling
    (lines 358-372). That branch emits the silent-SAFE
    ``summary: {"error": "No changes for staged"}`` envelope this probe
    pins. cmd_coupling requires an index (``ensure_index()`` at line 336),
    so we run ``roam index`` after git init.
    """
    proj = tmp_path / "clean-coupling-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def greet(name):\n    return f'hi {name}'\n")
    git_init(proj)
    # Index so ensure_index() doesn't bail.
    index_in_process(proj)
    return proj


def _invoke_coupling(proj, *args, json_mode=False):
    """Run ``roam coupling`` in-process via CliRunner, cwd=proj.

    Mirrors the canonical invocation pattern used by the W805 sister
    suites so the W805-MMMMM probe exercises the same surface as the
    base regression suite.
    """
    runner = CliRunner()
    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.append("coupling")
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
# W978 verification -- cmd_coupling actually consumes ``get_changed_files``.
# If cmd_coupling is refactored to a different helper, this test surfaces
# the structural drift before the W805-MMMMM pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdCouplingConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_coupling imports + calls
    ``get_changed_files``. Source-level invariant elevating W805-MMMMM
    from a coincidental shape match to a structural shared-helper audit."""

    def test_cmd_coupling_consumes_get_changed_files(self):
        """Source-level check: cmd_coupling imports + calls
        get_changed_files."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_coupling.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-MMMMM W978-precondition: cmd_coupling must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files(" in src, (
            "W805-MMMMM W978-precondition: cmd_coupling must call "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )

    def test_cmd_coupling_has_single_call_site(self):
        """Source-level: cmd_coupling calls the shared helper
        ``get_changed_files`` exactly ONCE, threading both ``staged``
        and ``commit_range`` keywords through the single site.
        Distinguishing axis from W805-HHHHH (two call sites unioned via
        set()) and W805-JJJJJ (two mutually-exclusive branches):
        cmd_coupling collapses --staged AND --against into a single
        call site."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_coupling.py").read_text(
            encoding="utf-8"
        )
        matches = list(re.finditer(r"get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 1, (
            f"W805-MMMMM: cmd_coupling should have exactly one shared-"
            f"helper get_changed_files(root, ...) call site (threading "
            f"staged + commit_range kwargs); got {len(matches)}. If a "
            f"refactor split into separate branches, re-audit the "
            f"shared-helper family shape."
        )


# ---------------------------------------------------------------------------
# W805-CCCCC-echo BLOCKER probe -- the ``roam coupling --staged``
# invocation must NOT raise TypeError. W805-CCCCC found a cmd_why_slow:168
# signature bug (``get_changed_files(base=base)`` -- both positional
# ``root`` missing AND ``base`` not an accepted keyword). Probe whether
# cmd_coupling has the same class of upstream bug. The source audit says
# NO -- line 357 is ``get_changed_files(root, staged=staged,
# commit_range=commit_range)`` which is correct -- but pin the invariant
# so future refactors that introduce a similar TypeError-class signature
# break get caught immediately.
# ---------------------------------------------------------------------------


class TestChangedModeNoSignatureTypeError:
    """``roam coupling --staged`` invocation (which calls
    ``get_changed_files(root, staged=True, commit_range=None)``) must
    not raise TypeError. This is the W805-CCCCC-echo probe -- confirms
    cmd_coupling is NOT BLOCKER-class like cmd_why_slow."""

    def test_coupling_staged_no_signature_typeerror(self, cli_runner, clean_indexed_project, monkeypatch):
        """``roam coupling --staged`` must exit cleanly (not raise
        TypeError)."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_coupling(clean_indexed_project, "--staged", json_mode=True)
        # Must not be a TypeError-class crash. Exit code 0 (clean) or 5
        # (gate failure) are both acceptable -- both prove the call
        # signature is correct. Anything else (especially TypeError
        # exception) is the BLOCKER class.
        assert result.exit_code in (0, 5), (
            f"W805-MMMMM BLOCKER probe (W805-CCCCC echo): "
            f"``roam coupling --staged`` must exit cleanly with 0 or 5, "
            f"not raise TypeError; got exit_code={result.exit_code} "
            f"output={result.output!r}"
        )

    def test_coupling_call_site_uses_correct_signature(self):
        """Source-level: the single shared-helper
        ``get_changed_files(root, staged=, commit_range=)`` call site
        must use the correct signature -- ``root`` positionally, valid
        keywords only (no ``base=`` keyword pattern from W805-CCCCC)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_coupling.py").read_text(
            encoding="utf-8"
        )
        matches = list(re.finditer(r"get_changed_files\(\s*root\b([^)]*)\)", src))
        assert len(matches) == 1, (
            f"W805-MMMMM: cmd_coupling must have exactly one shared-"
            f"helper get_changed_files(root, ...) call site; got "
            f"{len(matches)}"
        )
        # W805-CCCCC bug pattern: ``base=`` keyword (helper has no such
        # kwarg). Helper accepts ``base_ref=`` instead.
        call_args = matches[0].group(1).strip()
        assert "base=" not in call_args, (
            f"W805-MMMMM W805-CCCCC echo: cmd_coupling must NOT use the "
            f"``base=`` keyword (helper signature uses ``base_ref=``); "
            f"got call args: {call_args!r}"
        )


# ---------------------------------------------------------------------------
# REAL BUG #1: silent-SAFE family member.
#
# cmd_coupling's empty-files branch (lines 358-372) emits
# ``summary: {"error": "No changes for <label>"}`` with no closed-enum
# disclosure of the empty-diff resolution. The summary is
# indistinguishable between (a) clean staging area / empty commit range
# and (b) shared-helper silent-empty-list on git failure. Pinned strict.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """The empty-files branch (lines 358-372) inherits the silent-SAFE
    family shape from the shared helper. Today the summary carries only
    ``error`` -- no closed-enum ``state`` / ``git_error`` /
    ``resolution`` field disclosing the no-changes-vs-git-failure
    disambiguation. Pattern-1-Variant-D requires the no-changes path to
    be distinguishable. Pinned strict; graduates when a closed-enum
    disclosure is added."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-MMMMM REAL BUG #1 (silent-SAFE family member, "
            "12th structural consumer): "
            "src/roam/commands/cmd_coupling.py:358-372 emits "
            '``summary: {"error": "No changes for <label>"}`` with no '
            "closed-enum state / git_error / resolution field on the "
            "empty-files branch. The summary is indistinguishable "
            "between (a) clean staging area / empty commit range and "
            "(b) shared-helper silent-empty-list on git failure "
            "(returncode != 0 OR FileNotFoundError). "
            "Pattern-1-Variant-D requires the no-changes path to "
            "disclose the resolution state. Pinned strict; graduates "
            "when ``summary.state`` (e.g. ``no_changes`` / "
            "``git_unavailable`` / ``empty_diff``) or "
            "``summary.resolution`` is added on the empty-files branch."
        ),
    )
    def test_empty_diff_emits_state_or_resolution(self, cli_runner, clean_indexed_project, monkeypatch):
        """Empty-diff envelope must emit ``summary.state`` or
        ``summary.resolution`` to disambiguate the clean-staging-area
        path from the git-failure silent-empty-list path."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_coupling(clean_indexed_project, "--staged", json_mode=True)
        # Exit 0 expected (empty diff -> no changed files).
        assert result.exit_code == 0, f"W805-MMMMM: unexpected exit_code={result.exit_code} output={result.output!r}"
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
            f"W805-MMMMM Pattern-1-V-D: empty-files branch must emit a "
            f"closed-enum disclosure (summary.state OR summary.git_error "
            f"OR summary.resolution) to disambiguate clean-staging-area "
            f"vs git-failure; got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


class TestBogusRefDistinctFromEmptyDiff:
    """cmd_coupling exposes ``--against <ref>`` which is DIRECTLY
    reachable from a bogus ref today (unlike W805-JJJJJ where the
    surface only exposed ``--changed``). A bogus-ref invocation
    (``--against bogus-ref-foo``) triggers ``git diff bogus-ref-foo``
    with returncode != 0, the shared helper silently returns ``[]``,
    and cmd_coupling emits ``summary.error: 'No changes for
    bogus-ref-foo'`` -- INDISTINGUISHABLE from a legitimate empty
    commit range. Pin the LATENT invariant that the empty-files branch
    must disambiguate clean-tree vs bogus-ref."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-MMMMM REAL BUG #1 ECHO (bogus-ref directly reachable): "
            "cmd_coupling's empty-files branch (lines 358-372) emits "
            "the same envelope shape for (a) ``--against HEAD~1..HEAD`` "
            "on a single-commit repo (legitimate empty range) and "
            "(b) ``--against bogus-ref-foo`` on any repo (git failure, "
            "silent-empty-list from shared helper). cmd_coupling is the "
            "FIRST shared-helper consumer where the bogus-ref path is "
            "directly reachable through the public CLI surface. Pinned "
            "strict; graduates when a state / git_error / resolution "
            "field disambiguates the two paths."
        ),
    )
    def test_bogus_ref_distinct_from_empty_diff(self, cli_runner, clean_indexed_project, monkeypatch):
        """``--against bogus-ref-foo`` envelope must carry a
        disambiguation field so it's distinguishable from a legitimate
        empty commit range."""
        monkeypatch.chdir(clean_indexed_project)
        result = _invoke_coupling(
            clean_indexed_project,
            "--against",
            "bogus-ref-that-does-not-exist-w805mmmmm",
            json_mode=True,
        )
        # Exit 0 expected today -- the shared helper silently returns
        # ``[]`` and cmd_coupling treats it as "no changes".
        assert result.exit_code == 0, f"W805-MMMMM: unexpected exit_code={result.exit_code} output={result.output!r}"
        data = json.loads(result.output)
        summary = data["summary"]
        # The disambiguation field must be present so a bogus ref can
        # be distinguished from a legitimate empty commit range.
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-MMMMM bogus-ref Pattern-1-V-D: ``--against "
            f"bogus-ref-...`` envelope must emit summary.state OR "
            f"summary.git_error OR summary.resolution to be "
            f"distinguishable from a legitimate empty commit range; "
            f"got state={state!r} git_error={git_error!r} "
            f"resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-JJJJJ + W805-HHHHH + W805-EEEEE +
# W805-CCCCC invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper (or cmd_coupling call site) MUST NOT
# perturb the sister suites.
# ---------------------------------------------------------------------------


class TestW805JJJJJInvariantsPreserved:
    """Sister cross-check: cmd_suggest_reviewers's empty-files branch
    (lines 338-353) still emits the silent-SAFE ``verdict: 'No changed
    files found'`` envelope pinned by W805-JJJJJ. A drive-by fix to
    cmd_coupling MUST NOT silently repair (or obscure) the
    cmd_suggest_reviewers silent-SAFE shape -- that is a
    separately-tracked sister pin."""

    def test_cmd_suggest_reviewers_still_consumes_shared_helper(self):
        """Source-level: cmd_suggest_reviewers still calls
        ``get_changed_files(root, ...)`` on both branches (W805-JJJJJ
        W978-precondition)."""
        src = (
            Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_suggest_reviewers.py"
        ).read_text(encoding="utf-8")
        assert "from roam.commands.changed_files import" in src, (
            "W805-MMMMM sister cross-check: cmd_suggest_reviewers must "
            "still import from roam.commands.changed_files (W805-JJJJJ "
            "W978-precondition)."
        )
        matches = list(re.finditer(r"get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 2, (
            f"W805-MMMMM sister cross-check: cmd_suggest_reviewers must "
            f"still have two shared-helper get_changed_files(root, ...) "
            f"call sites (W805-JJJJJ W978-precondition); got "
            f"{len(matches)}"
        )

    def test_cmd_syntax_check_still_consumes_shared_helper(self):
        """Source-level: cmd_syntax_check still calls
        ``get_changed_files(root, ...)`` (W805-HHHHH W978-precondition)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_syntax_check.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-MMMMM sister cross-check: cmd_syntax_check must still "
            "import from roam.commands.changed_files (W805-HHHHH W978-"
            "precondition)."
        )
        matches = list(re.finditer(r"(?<!_)get_changed_files\(\s*root\b[^)]*\)", src))
        assert len(matches) == 2, (
            f"W805-MMMMM sister cross-check: cmd_syntax_check must "
            f"still have two shared-helper get_changed_files(root, ...) "
            f"call sites (W805-HHHHH W978-precondition); got "
            f"{len(matches)}"
        )

    def test_cmd_verify_still_consumes_shared_helper(self):
        """Source-level: cmd_verify still calls
        ``get_changed_files(root)`` (W805-EEEEE W978-precondition)."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_verify.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-MMMMM sister cross-check: cmd_verify must still "
            "import from roam.commands.changed_files (W805-EEEEE W978-"
            "precondition)."
        )
        assert "get_changed_files(root)" in src, (
            "W805-MMMMM sister cross-check: cmd_verify must still call "
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
            "W805-MMMMM sister cross-check: cmd_why_slow must still "
            "call get_changed_files (W805-CCCCC W978-precondition)."
        )
        any_has_base_kw = any("base=" in m.group(1) for m in matches)
        assert any_has_base_kw, (
            "W805-MMMMM sister cross-check: cmd_why_slow's W805-CCCCC "
            "signature bug appears to have been repaired (no ``base=`` "
            "keyword in any get_changed_files call). If this is "
            "intentional, graduate the W805-CCCCC xfail-strict pins in "
            "tandem."
        )
