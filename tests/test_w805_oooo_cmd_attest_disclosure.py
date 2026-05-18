"""W805-OOOO -- shared-helper silent-SAFE probe on ``roam attest``.

Ninety-third-in-batch W805 sweep. FOURTH candidate -- and THIRD CONFIRMED
strict consumer -- for the shared-helper resolution-disclosure family.

Family lineage entering this probe:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via the shared
    helper returning ``[]`` on all failure classes.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE silent-SAFE
    (no ``state`` field at all).
  * W805-MMMM (cmd_ws) -- DISCONFIRMED. cmd_ws does not consume
    ``get_changed_files`` (workspace-management on indexed DBs).
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper
    on the analogous-but-not-shared axis.

Family stood 2-strong on the strict ``get_changed_files`` axis at the
start of this probe (cmd_diff + cmd_pr_diff). cmd_attest -- a proof-
carrying PR attestation aggregating blast / risk / breaking / budget /
fitness / tests / effects -- is the proof-bundle/CGA family's leading
shared-helper candidate.

W978 first-hypothesis: cmd_attest is a strict shared-helper consumer
--------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_attest.py`` head-to-tail:

  * Lines 22-26: ``from roam.commands.changed_files import
    get_changed_files, is_test_file, resolve_changed_to_db``. The
    import IS the shared helper used by cmd_diff (W805-EEEE) and
    cmd_pr_diff (W805-JJJJ).
  * Line 768: ``changed = get_changed_files(root, staged=staged,
    commit_range=commit_range)``. Same call shape as
    ``cmd_diff.py:475`` (W805-EEEE) and ``cmd_pr_diff.py:78``
    (W805-JJJJ). cmd_attest is a CONFIRMED consumer of the same
    fallible boundary.
  * Lines 769-805: ``if not changed:`` branch emits
    ``state: "no_changes"`` + ``partial_success: true`` +
    ``safe_to_merge: None`` -- the SAME envelope shape on both
    clean-tree and bogus-ref paths.

W978 finding: CONFIRMED. cmd_attest inherits the W805-EEEE silent-SAFE
shape via the shared helper. This is the THIRD strict consumer; the
shared-helper family on the ``get_changed_files`` axis elevates from
2-strong to 3-strong (cmd_diff + cmd_pr_diff + cmd_attest). The pattern
is now STRUCTURAL: any consumer of ``get_changed_files`` inherits
silent-SAFE on bogus-ref unless the helper is upgraded to return
``(paths, error_kind)``.

Probe results (CliRunner against a clean indexed project)
---------------------------------------------------------

* ``roam --json attest`` on clean tree (uncommitted):
  exit 0, ``verdict: "no changes found for uncommitted (risk_level
  low)"``, ``state: "no_changes"``, ``partial_success: true``,
  ``safe_to_merge: null``, ``risk_level_canonical: "low"``,
  ``risk_rank: 1``. NO ``resolution``, NO ``git_error`` field.

* ``roam --json attest nonexistent_branch..HEAD``:
  exit 0. ``summary`` BYTE-IDENTICAL to clean-tree on every machine-
  state field (``state``, ``partial_success``, ``safe_to_merge``,
  ``risk_level_canonical``, ``risk_rank``). Only the verdict-text
  echoes the user-supplied label, which is a TEXT-ONLY difference --
  agents reading machine-state fields see no degradation.

* ``roam --json attest --staged`` on clean tree (no staged files):
  same shape.

Mild improvement over W805-EEEE (cmd_diff) AND W805-JJJJ (cmd_pr_diff)
----------------------------------------------------------------------

cmd_attest's degraded-resolution path is slightly less severe than the
two prior shared-helper family members:

  * cmd_diff (W805-EEEE): ``safe_to_merge`` is not a field; the verdict
    is the only signal. ``state: "no_changes"``.
  * cmd_pr_diff (W805-JJJJ): NO ``state`` field at all; the worst
    shape observed in the W805 sweep.
  * cmd_attest (W805-OOOO): ``state: "no_changes"`` PLUS
    ``safe_to_merge: null``. The null carries useful signal -- an
    agent calling ``bool(summary["safe_to_merge"])`` reads False
    instead of True. But the null is ambiguous between clean-tree
    and bogus-ref, and ``partial_success: true`` fires on BOTH paths,
    so the Pattern-1-V-D contract (closed-enum disclosure
    distinguishing degraded from clean) is still violated.

The xfail-strict pin matches the W805-JJJJ shape: bogus-ref must emit
a closed-enum ``state`` value distinct from ``"no_changes"`` OR a
non-empty ``resolution``/``git_error`` field. Today both paths share
``state: "no_changes"`` so the pin is RED until the shared helper
returns ``(paths, error_kind)``.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_attest.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import``
case-insensitive): NO matches. The function-scoped lazy imports inside
``_collect_blast_radius`` (lines 119-122), ``_collect_risk`` (lines
166-178), ``_collect_breaking`` (lines 318-326), ``_collect_budget_
evidence`` (lines 393-397), ``_collect_affected_tests_evidence`` (line
362), ``_compute_verdict`` and the atomic_io imports (lines 990, 1002,
1162) are legitimate cost-deferrals -- networkx is the heaviest -- or
follow the LazyGroup pattern. The atomic_io imports are explicitly
documented (W531 / R28 substrate `unsafe_mutation` guard). Clean on W907.

Shared-helper family update
---------------------------

Before this probe:
  * W805-EEEE: cmd_diff (shared-helper consumer).
  * W805-JJJJ: cmd_pr_diff (shared-helper consumer) -- 2nd strict
    consumer.
  * W805-MMMM: cmd_ws (DISCONFIRMED).
  * W805-AAAA: cmd_delete_check (independent helper).

After this probe:
  * W805-OOOO: cmd_attest CONFIRMED (shared-helper consumer) --
    THIRD strict ``get_changed_files`` consumer.
  * Shared-helper family elevates to 3-STRONG on the strict
    ``get_changed_files`` axis (cmd_diff + cmd_pr_diff + cmd_attest).
    Pattern is now STRUCTURAL. Total family across all axes is
    4-strong (counting cmd_delete_check on the independent-but-
    analogous axis).

W805 sweep update
-----------------

W805 sweep yield ~49/50. Strict-consumer family is now structural
(3-strong). Future fix: ``get_changed_files`` should be upgraded to
``(paths, error_kind)`` -- a single change atomically unblocks three
consumers (cmd_diff, cmd_pr_diff, cmd_attest).

Next W805 sweep candidate (W805-PPPP)
-------------------------------------

Per the canonical strict-consumer list at W805-MMMM (19 modules),
remaining unprobed candidates include: cmd_test_gaps, cmd_orchestrate
(W805-DDDD scope but NOT shared-helper axis), cmd_adversarial,
cmd_boundary, cmd_why_slow, cmd_file, cmd_verify, cmd_syntax_check,
cmd_suggest_reviewers, cmd_coupling, cmd_affected_tests, cmd_affected,
cmd_preflight (W607-R in-flight, NOT a candidate), cmd_plan. W805-PPPP
candidate: cmd_test_gaps -- test-impact selection on a changed-files
boundary is a natural inheritor of the silent-SAFE shape.
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

    Used as the baseline -- the W805-OOOO bug is that a bogus-ref
    invocation should NOT be byte-identical to this clean-tree case on
    machine-state fields.
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
# W978 verification -- cmd_attest actually consumes get_changed_files.
# This test asserts the source-level contract that founds the W805-OOOO
# probe. If cmd_attest is refactored to a different helper, this test
# graduates the W805-OOOO pin to "not applicable" rather than letting
# the bug class hide behind a stale assertion.
# ---------------------------------------------------------------------------


class TestCmdAttestConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_attest is a confirmed
    consumer of ``get_changed_files`` from
    ``src/roam/commands/changed_files.py``. This is the source-level
    invariant that elevates W805-OOOO from a coincidental shape match
    to a structural shared-helper class member."""

    def test_cmd_attest_consumes_get_changed_files(self):
        """Source-level check: cmd_attest imports + calls get_changed_files.

        Fails if a refactor moves cmd_attest onto a different helper.
        At that point the W805-OOOO pin is structurally stale and the
        new helper must be re-audited for the same bug class.
        """
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_attest.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-OOOO W978-precondition: cmd_attest must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files" in src, (
            "W805-OOOO W978-precondition: cmd_attest must reference "
            "get_changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files(root" in src, (
            "W805-OOOO W978-precondition: cmd_attest must CALL "
            "get_changed_files(root, ...); if the call site moved, "
            "re-audit the shared-helper family membership."
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash on degenerate diff-source paths.
# Guard-rail: any future W805-OOOO fix must not reintroduce the
# empty-stdout crash class while adding disclosure on top.
# ---------------------------------------------------------------------------


class TestAttestSourceNoCrash:
    """Bogus diff-source paths must always emit a structured envelope,
    never crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_bogus_commit_range_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range: non-empty stdout, parseable JSON, no exception."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, f"bogus commit-range must exit 0; got {result.exit_code}\n{result.output}"
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on bogus-range"
        data = parse_json_output(result, "attest")
        assert isinstance(data, dict)

    def test_staged_on_clean_tree_no_crash(self, cli_runner, clean_indexed_project, monkeypatch):
        """``--staged`` on a clean tree: non-empty stdout, parseable JSON."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "--staged"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on --staged clean"
        data = parse_json_output(result, "attest")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Pattern-1-V-D resolution disclosure on the diff-source axis.
# REAL BUG pinned strict.
#
# cmd_attest inherits silent-SAFE from get_changed_files. The envelope
# emits ``state: "no_changes"`` + ``partial_success: true`` +
# ``safe_to_merge: null`` on BOTH clean-tree AND bogus-ref paths. Same
# class as cmd_diff (W805-EEEE).
# ---------------------------------------------------------------------------


class TestBogusCommitRangeStateDisclosure:
    """The bogus-commit-range path produces an envelope indistinguishable
    from a clean working tree on every machine-state field. There is no
    closed-enum disclosure separating the two paths."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-OOOO REAL BUG: src/roam/commands/cmd_attest.py:769-805 "
            "(the ``if not changed:`` branch downstream of "
            '``get_changed_files``) emits ``state: "no_changes"`` + '
            "``partial_success: true`` + ``safe_to_merge: null`` on a "
            "bogus commit-range -- byte-identical to the clean-tree "
            "envelope on every machine-state field. The root cause is "
            "``src/roam/commands/changed_files.py:142,145`` swallowing "
            "``returncode != 0`` / FileNotFoundError / TimeoutExpired into "
            "an empty list. Pattern-1-V-D silent-success-on-degraded-"
            "resolution. THIRD strict shared-helper consumer; FAMILY IS "
            "NOW STRUCTURAL (3-strong on the get_changed_files axis: "
            "cmd_diff + cmd_pr_diff + cmd_attest). Pinned strict; "
            "graduates when the bogus-ref path emits ``state`` with a "
            "non-``no_changes`` closed-enum value -- ideally atomically "
            "with the W805-EEEE and W805-JJJJ graduation when the shared "
            "helper is upgraded to ``(paths, error_kind)``."
        ),
    )
    def test_bogus_commit_range_state_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit-range path must emit a non-``no_changes`` ``state``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        state = summary.get("state")
        # The bug: state is "no_changes" -- byte-identical to clean tree.
        assert state and state != "no_changes", (
            f"W805-OOOO Pattern-1-V-D: bogus-commit-range path must emit "
            f"a non-``no_changes`` summary.state to distinguish typo'd "
            f"ref from genuinely clean working tree; got {state!r}"
        )


class TestBogusCommitRangeResolutionDisclosure:
    """Mirror axis: a bogus positional commit_range must emit a closed-
    enum ``resolution`` field, since the bogus-ref invocation IS a
    degraded-resolution path under the shared helper."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-OOOO REAL BUG (resolution axis): bogus commit_range "
            "path emits no ``resolution`` field. Pattern-1-V-D contract "
            "requires AT LEAST ONE closed-enum disclosure (state OR "
            "resolution) on the degraded-resolution path. Pinned strict; "
            "graduates when the envelope distinguishes bogus-ref from "
            "clean-tree on either field."
        ),
    )
    def test_bogus_commit_range_resolution_disclosure(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus commit_range must emit ``summary.resolution`` OR a non-empty state."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "totally_fake_ref..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        resolution = summary.get("resolution")
        state = summary.get("state")
        assert (resolution and isinstance(resolution, str) and resolution.strip()) or (
            state and state != "no_changes"
        ), (
            f"W805-OOOO Pattern-1-V-D: bogus commit_range path must emit "
            f"summary.resolution OR a non-``no_changes`` summary.state; "
            f"got resolution={resolution!r} state={state!r}"
        )


class TestSilentSafeInheritedFromSharedHelper:
    """Family-confirmation test: cmd_attest inherits the same silent-SAFE
    shape as cmd_diff and cmd_pr_diff via the shared ``get_changed_files``
    helper. THIRD strict consumer -- elevates the family to STRUCTURAL.
    Pins the inheritance so a fix to the shared helper unblocks ALL THREE
    consumers atomically."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-OOOO FAMILY-CONFIRMATION: cmd_attest's bogus-ref path "
            "emits no ``git_error`` field -- the same gap W805-EEEE pins "
            "on cmd_diff and W805-JJJJ pins on cmd_pr_diff. The shared "
            "helper ``src/roam/commands/changed_files.py:131-146`` returns "
            "an empty list on three distinct failure classes "
            "(returncode != 0, FileNotFoundError, TimeoutExpired). All "
            "THREE consumers (cmd_diff, cmd_pr_diff, cmd_attest) inherit "
            "silent-SAFE -- the family is now STRUCTURAL. Pinned strict; "
            "graduates when ``get_changed_files`` returns a "
            "``(paths, error_kind)`` tuple and cmd_attest surfaces "
            "``summary.git_error`` on the failure branch."
        ),
    )
    def test_bogus_ref_envelope_has_git_error_field(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref path must emit ``summary.git_error`` distinct from clean tree."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        git_error = summary.get("git_error")
        assert git_error and isinstance(git_error, str) and git_error.strip(), (
            f"W805-OOOO: bogus-ref path must emit summary.git_error "
            f"distinct from clean tree (which has no git failure); "
            f"got {git_error!r}"
        )


class TestCleanTreeDistinctFromBogusRef:
    """Pattern-2 invariant: a genuinely-clean working tree and a
    git-error path MUST produce distinguishable envelopes. Today they
    are byte-identical on every machine-state field cmd_attest emits."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-OOOO REAL BUG (invariant): clean-tree envelope and "
            "bogus-ref envelope are byte-identical on every machine-state "
            "field (state, resolution, git_error, safe_to_merge, "
            "partial_success). Only the verdict-text echoes the user-"
            "supplied label, which is a TEXT-ONLY difference -- agents "
            "reading machine-state fields see no degradation. Pattern-2 "
            "silent-fallback contract violated. Pinned strict; graduates "
            "when the two envelopes differ on at least one closed-enum "
            "machine-state field."
        ),
    )
    def test_clean_tree_distinct_from_bogus_ref(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean-tree envelope must differ from bogus-ref envelope on a machine-state field."""
        monkeypatch.chdir(clean_indexed_project)
        clean_result = invoke_cli(
            cli_runner,
            ["attest"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        bogus_result = invoke_cli(
            cli_runner,
            ["attest", "nonexistent_branch..HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        clean_summary = parse_json_output(clean_result, "attest")["summary"]
        bogus_summary = parse_json_output(bogus_result, "attest")["summary"]

        machine_state_fields = (
            "state",
            "resolution",
            "git_error",
            "safe_to_merge",
            "partial_success",
        )
        differing = [f for f in machine_state_fields if clean_summary.get(f) != bogus_summary.get(f)]
        assert differing, (
            f"W805-OOOO Pattern-2: clean-tree and bogus-ref envelopes "
            f"must differ on at least one machine-state field "
            f"({machine_state_fields}); got identical values "
            f"clean={ {f: clean_summary.get(f) for f in machine_state_fields} } "
            f"bogus={ {f: bogus_summary.get(f) for f in machine_state_fields} }"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-checks -- W805-EEEE + W805-JJJJ + W805-MMMM
# invariants must stay green. A future fix to the shared
# ``get_changed_files`` helper (which would graduate W805-EEEE,
# W805-JJJJ, and W805-OOOO atomically) MUST NOT perturb the W805-MMMM
# disconfirmation invariant nor the W805-AAAA cmd_delete_check shape.
# ---------------------------------------------------------------------------


class TestW805JjjjInvariantsPreserved:
    """Sister cross-check: cmd_pr_diff's W805-JJJJ no-changes envelope
    shape is preserved. The clean-tree branch still emits the canonical
    no-changes envelope."""

    def test_pr_diff_clean_tree_still_emits_no_changes(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_pr_diff clean tree still emits ``no changes`` verdict."""
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
        # The pre-W805-JJJJ contract: clean tree verdict mentions
        # ``no change``. Must stay after any future shared-helper fix.
        assert "no change" in summary.get("verdict", "").lower(), (
            f"W805-OOOO sister cross-check: cmd_pr_diff clean-tree must "
            f"still emit ``no change`` verdict; "
            f"got {summary.get('verdict')!r}"
        )
        assert summary.get("partial_success") is False


class TestW805MmmmInvariantsPreserved:
    """Sister cross-check: cmd_ws disconfirmation invariant.

    The W805-MMMM probe found cmd_ws is NOT a shared-helper consumer.
    The invariant it pins is the ABSENCE of consumption: cmd_ws.py must
    not silently grow a ``get_changed_files`` call without re-auditing
    for inherited silent-SAFE. This guard test re-asserts that the
    cmd_ws.py source does NOT import or call ``get_changed_files``.
    """

    def test_cmd_ws_does_not_consume_get_changed_files(self):
        """cmd_ws must NOT import ``get_changed_files``.

        If a future refactor introduces such a call, this test flips
        RED -- the correct signal to re-open the family-membership
        question and audit cmd_ws for inherited silent-SAFE."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_ws.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" not in src, (
            "W805-OOOO sister cross-check: cmd_ws.py must NOT import "
            "from roam.commands.changed_files; if this changed, "
            "re-audit cmd_ws for inherited silent-SAFE on the shared-"
            "helper axis."
        )
        assert "get_changed_files(" not in src, (
            "W805-OOOO sister cross-check: cmd_ws.py must NOT call "
            "get_changed_files(...); if this changed, re-audit cmd_ws "
            "for inherited silent-SAFE."
        )


# ---------------------------------------------------------------------------
# Positive regression -- clean diff sources still produce real verdicts.
# Guards against an over-correcting fix-forward.
# ---------------------------------------------------------------------------


class TestCleanAttestPositiveRegression:
    """Positive regression: clean tree still emits the canonical
    no-changes envelope. This is the pre-W805-OOOO contract -- it
    must stay even after the bogus-ref path is disambiguated."""

    def test_clean_tree_still_emits_no_changes_state(self, cli_runner, clean_indexed_project, monkeypatch):
        """Clean tree still emits ``state: "no_changes"`` + ``safe_to_merge: null``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["attest"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "attest")
        summary = data["summary"]
        # cmd_attest's clean-tree verdict mentions ``no changes found``.
        assert "no changes" in summary.get("verdict", "").lower(), (
            f"Positive regression: clean-tree verdict must mention ``no changes``; got {summary.get('verdict')!r}"
        )
        assert summary.get("state") == "no_changes"
        assert summary.get("safe_to_merge") is None
        # W641-followup-D canonical risk-LEVEL projection: clean-tree
        # safe-floors to ``low`` (W531 CI-safety) and risk_rank == 1.
        assert summary.get("risk_level_canonical") == "low"
        assert summary.get("risk_rank") == 1
