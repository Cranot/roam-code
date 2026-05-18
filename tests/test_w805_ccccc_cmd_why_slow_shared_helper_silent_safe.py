"""W805-CCCCC -- shared-helper silent-SAFE probe on ``roam why-slow``.

Hundred-and-seventh-in-batch W805 sweep. NINTH potential strict consumer
for the shared-helper resolution-disclosure family on the
``get_changed_files`` axis -- DISCONFIRMED BY A STRICTLY-WORSE
UPSTREAM BUG.

Family lineage entering this probe:

  * W805-EEEE (cmd_diff) -- CATASTROPHIC silent-SAFE via shared helper.
  * W805-JJJJ (cmd_pr_diff) -- STRICTLY MORE SEVERE (no ``state`` field).
  * W805-OOOO (cmd_attest) -- THIRD strict consumer.
  * W805-RRRR (cmd_test_gaps) -- FOURTH strict consumer.
  * W805-SSSS (cmd_affected_tests) -- FIFTH (STRICTLY WORST -- plain text
    in --json mode).
  * W805-VVVV (cmd_affected) -- SIXTH (envelope shape, two call sites).
  * W805-XXXX (cmd_adversarial) -- SEVENTH (envelope shape).
  * W805-AAAAA (cmd_boundary) -- EIGHTH (envelope shape).
  * W805-AAAA (cmd_delete_check) -- independent ``_git_diff`` helper on
    the analogous-but-not-shared axis.

Family stood 8-STRONG STRUCTURAL on the strict ``get_changed_files`` axis
at the start of this probe.

W978 DISCONFIRM: cmd_why_slow's call is HARD-BROKEN, not silent-SAFE
--------------------------------------------------------------------

Source audit of ``src/roam/commands/cmd_why_slow.py`` head-to-tail:

  * Line 39: ``from roam.commands.changed_files import get_changed_files``.
    The import IS the shared helper used by all eight prior strict
    consumers.
  * Line 168: ONE call site, gated on ``--changed``:
    ``changed_files = set(get_changed_files(base=base))``.

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

The helper requires ``root: Path`` POSITIONALLY and uses ``base_ref=``,
NOT ``base=``. cmd_why_slow line 168 calls it as
``get_changed_files(base=base)`` -- BOTH parameters are wrong:

  1. ``root`` is missing (TypeError on missing required positional).
  2. ``base`` is not an accepted keyword (TypeError on unexpected
     keyword argument).

Live invocation (``why-slow --changed``) raises
``TypeError: get_changed_files() got an unexpected keyword argument
'base'``. The ``--changed`` path is HARD-BROKEN at the function-
signature level. The silent-SAFE behavior on the bogus-ref path is
UNREACHABLE because the call itself raises before the helper's
returncode != 0 silent-empty-list path can fire.

This is W978 DISCONFIRM by a STRICTLY-WORSE upstream bug. cmd_why_slow
IS a ``get_changed_files`` consumer (W978-precondition holds), but the
silent-SAFE FAMILY does NOT extend through it -- a hard TypeError is
fundamentally distinct from a silent-fallback verdict. The family
remains 8-STRONG STRUCTURAL.

Both real bugs are pinned strict below:

  1. ``--changed`` raises TypeError (BLOCKER-class; the path is non-
     functional today). Pinned via ``TestChangedPathRaisesTypeError``.
  2. The DESIGN intent (if signatures were fixed) inherits the silent-
     SAFE family member shape. Pinned as a forward-looking strict
     xfail via ``TestStateFieldOnFailure`` to capture the latent
     silent-SAFE that resurfaces the day the signature mismatch is
     fixed.

W907 verify-cycle (false-import-cycle hedge check)
--------------------------------------------------

Grep of cmd_why_slow.py for the W907 patterns (``avoid.*cycle`` /
``avoid.*circular`` / ``prevent.*import.*cycle`` / ``defer.*import`` /
``lazy.*import`` case-insensitive): zero matches. All imports are
top-level and bare. Clean on W907.

Shared-helper family update
---------------------------

Before this probe: 8-STRONG STRUCTURAL (EEEE / JJJJ / OOOO / RRRR /
SSSS / VVVV / XXXX / AAAAA) + 1 independent (AAAA).

After this probe: family STILL 8-STRONG STRUCTURAL (cmd_why_slow
disconfirmed by upstream TypeError) + 1 independent = 9 family members
total. cmd_why_slow is a BOUNDED-BY-WORSE-BUG case: latent member
once the TypeError is fixed.

W805 sweep update
-----------------

W805 sweep yield 54/54 (this probe = 54th). Strict-consumer family is
still 8-STRONG fully structural; cmd_why_slow surfaces a NEW bug class
(hard TypeError) that supersedes the silent-SAFE family-membership
question.

Next W805 sweep candidate (W805-DDDDD)
--------------------------------------

Remaining unprobed strict-consumer candidates per the W805-AAAAA
canonical-list ordering: cmd_verify, cmd_syntax_check,
cmd_suggest_reviewers, cmd_coupling, cmd_plan. W805-DDDDD candidate:
cmd_verify -- next-most-likely shared-helper consumer per the
W805-AAAAA strict-consumer ordering.
"""

from __future__ import annotations

import os
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


def _seed_runtime_stats(project_root: Path) -> None:
    """Seed one synthetic ``runtime_stats`` row so ``total_traced > 0``.

    Without this seed, cmd_why_slow short-circuits on line 143 with
    ``verdict: "NO RUNTIME DATA"`` BEFORE the shared helper is ever
    called -- the ``--changed`` code path is then unreachable. The
    seeded row is intentionally minimal: one symbol, one hot path,
    just enough for ``total_traced > 0`` so the line-167 ``if changed``
    branch executes.
    """
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    os.chdir(str(project_root))
    try:
        with open_db() as conn:
            conn.execute(
                """INSERT INTO runtime_stats
                   (symbol_name, file_path, call_count, p50_latency_ms,
                    p99_latency_ms, error_rate, trace_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("synthetic_fn", "app.py", 100, 1.0, 10.0, 0.0, "test"),
            )
            conn.commit()
    finally:
        os.chdir(old_cwd)


@pytest.fixture
def clean_indexed_project(tmp_path):
    """Indexed project with a clean working tree + one seeded runtime_stats row.

    cmd_why_slow's ``--changed`` path is only reachable when
    ``total_traced > 0``, otherwise the ``NO RUNTIME DATA`` short-
    circuit pre-empts the ``get_changed_files`` call. The seeded row
    makes the ``--changed`` path reachable so the TypeError on line 168
    is exercised.
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
    _seed_runtime_stats(proj)
    return proj


# ---------------------------------------------------------------------------
# W978 verification -- cmd_why_slow actually consumes get_changed_files.
# If cmd_why_slow is refactored to a different helper, this test surfaces
# the structural drift before the W805-CCCCC pin silently goes stale.
# ---------------------------------------------------------------------------


class TestCmdWhySlowConsumesSharedHelper:
    """W978 first-hypothesis verification: cmd_why_slow imports + calls
    ``get_changed_files``. Source-level invariant elevating W805-CCCCC
    from a coincidental shape match to a structural shared-helper
    audit. The CALL site is broken (TypeError -- see TestChangedPath...
    below) but the import + call pattern qualifies cmd_why_slow as a
    latent member of the silent-SAFE family."""

    def test_cmd_why_slow_consumes_get_changed_files(self):
        """Source-level check: cmd_why_slow imports + calls get_changed_files."""
        src = (Path(__file__).resolve().parent.parent / "src" / "roam" / "commands" / "cmd_why_slow.py").read_text(
            encoding="utf-8"
        )
        assert "from roam.commands.changed_files import" in src, (
            "W805-CCCCC W978-precondition: cmd_why_slow must import from "
            "roam.commands.changed_files; if this changed, re-audit the "
            "shared-helper family membership."
        )
        assert "get_changed_files(" in src, (
            "W805-CCCCC W978-precondition: cmd_why_slow must call "
            "get_changed_files; if this changed, re-audit the shared-"
            "helper family membership."
        )


# ---------------------------------------------------------------------------
# Sanity / W978 -- the ``--changed`` path on cmd_why_slow today raises
# TypeError because the call signature on line 168 is broken in TWO ways
# (missing required ``root`` positional, unaccepted ``base`` keyword).
# This is a STRICTLY-WORSE upstream bug class than silent-SAFE.
# REAL BUG #1 pinned strict.
# ---------------------------------------------------------------------------


class TestChangedPathRaisesTypeError:
    """The ``roam why-slow --changed`` invocation today raises TypeError
    because cmd_why_slow line 168 calls
    ``get_changed_files(base=base)`` -- but the helper signature
    requires ``root: Path`` positionally and uses ``base_ref=`` not
    ``base=``. This is BLOCKER-class: the ``--changed`` flag is
    non-functional. Pinned strict; graduates when the call signature
    is fixed."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CCCCC REAL BUG #1 (HARD-BROKEN): "
            "src/roam/commands/cmd_why_slow.py:168 calls "
            "``get_changed_files(base=base)`` but the helper at "
            "src/roam/commands/changed_files.py:98-105 requires "
            "``root: Path`` POSITIONALLY and uses ``base_ref=`` NOT "
            "``base=``. Live ``--changed`` invocation raises ``TypeError: "
            "get_changed_files() got an unexpected keyword argument "
            "'base'``. The ``--changed`` path is non-functional today --"
            "STRICTLY-WORSE than the silent-SAFE family pattern. Pinned "
            "strict; graduates when the call signature is corrected "
            "(``get_changed_files(project_root, base_ref=base)`` or "
            "similar)."
        ),
    )
    def test_changed_flag_exits_cleanly(self, cli_runner, clean_indexed_project, monkeypatch):
        """``roam why-slow --changed`` must exit 0 (not raise TypeError)."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["why-slow", "--changed", "--base", "HEAD"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"W805-CCCCC: ``--changed`` path must exit 0, not raise; "
            f"got exit_code={result.exit_code} output={result.output!r}"
        )


# ---------------------------------------------------------------------------
# Forward-looking family-confirmation -- once the TypeError on line 168
# is fixed, cmd_why_slow will inherit the silent-SAFE family member
# shape (bogus-ref returns []; "NO CHANGES" verdict indistinguishable
# from clean-tree). Pin the LATENT bug class so the day the signature
# fix lands, the silent-SAFE family pin is enforced without a separate
# audit.
# ---------------------------------------------------------------------------


class TestStateFieldOnFailure:
    """Forward-looking pin: once the TypeError is fixed, the bogus-ref
    path must emit a closed-enum ``state`` / ``git_error`` /
    ``resolution`` field disclosing the degraded-resolution branch.
    Today the test xfails because the TypeError pre-empts the silent-
    SAFE branch; the day the signature is fixed AND the family is also
    fixed, this graduates."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-CCCCC REAL BUG #2 (LATENT silent-SAFE family member): "
            "once cmd_why_slow line 168 is repaired, the ``if not "
            "changed_files`` branch (lines 169-185) inherits the silent-"
            "SAFE family shape from the shared helper. The summary today "
            "(lines 175-181) carries only ``verdict / base / total_traced "
            "/ hotspots`` -- no closed-enum state / git_error / "
            "resolution field disclosing the failure class. Pattern-1-V-D "
            "requires the bogus-ref path to be distinguishable from the "
            "clean-tree path. Pinned strict; graduates when the call "
            "signature is fixed AND a closed-enum disclosure is added on "
            "the failure branch."
        ),
    )
    def test_bogus_ref_emits_state_or_git_error(self, cli_runner, clean_indexed_project, monkeypatch):
        """Bogus-ref envelope must emit ``summary.state`` or ``summary.git_error``."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["why-slow", "--changed", "--base", "totally-bogus-ref-99"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        # If the TypeError is still live, exit_code != 0 here -- the
        # xfail-strict still pins the latent bug correctly because the
        # final ``state/git_error/resolution`` assertion never reaches a
        # successful resolution.
        if result.exit_code != 0:
            # TypeError is still live: latent silent-SAFE not yet
            # reachable; xfail-strict captures the latent bug.
            pytest.fail(
                f"W805-CCCCC: ``--changed`` path raised before silent-SAFE could be exercised: {result.output!r}"
            )
        data = parse_json_output(result, "why-slow")
        summary = data["summary"]
        state = summary.get("state")
        git_error = summary.get("git_error")
        resolution = summary.get("resolution")
        assert (
            (state and isinstance(state, str) and state.strip())
            or (git_error and isinstance(git_error, str) and git_error.strip())
            or (resolution and isinstance(resolution, str) and resolution.strip())
        ), (
            f"W805-CCCCC Pattern-1-V-D: bogus-ref path must emit a closed-"
            f"enum disclosure (summary.state OR summary.git_error OR "
            f"summary.resolution); got state={state!r} "
            f"git_error={git_error!r} resolution={resolution!r}"
        )


# ---------------------------------------------------------------------------
# Sister suite cross-check -- W805-AAAAA invariants must stay green. A
# future fix to the shared ``get_changed_files`` helper (or cmd_why_slow
# call signature) MUST NOT perturb the clean-tree no-findings envelope
# cmd_boundary emits.
# ---------------------------------------------------------------------------


class TestW805AaaaaInvariantsPreserved:
    """Sister cross-check: cmd_boundary's W805-AAAAA clean-tree envelope
    shape is preserved. The clean-tree path still emits a parseable JSON
    envelope with the canonical ``"0 boundary findings"`` verdict or
    ``state: no_imports`` on the empty-corpus path."""

    def test_boundary_clean_tree_still_emits_no_findings_verdict(self, cli_runner, clean_indexed_project, monkeypatch):
        """cmd_boundary clean-tree still emits ``0 boundary findings`` verdict."""
        monkeypatch.chdir(clean_indexed_project)
        result = invoke_cli(
            cli_runner,
            ["boundary", "--changed-range", "pr", "--base-ref", "main"],
            cwd=clean_indexed_project,
            json_mode=True,
        )
        assert result.exit_code == 0
        data = parse_json_output(result, "boundary")
        summary = data["summary"]
        verdict = summary.get("verdict", "")
        assert "0 boundary findings" in verdict or summary.get("state") == "no_imports", (
            f"W805-CCCCC sister cross-check: cmd_boundary clean-tree "
            f"must still emit ``0 boundary findings`` verdict or "
            f"``state: no_imports``; got verdict={verdict!r} "
            f"state={summary.get('state')!r}"
        )
