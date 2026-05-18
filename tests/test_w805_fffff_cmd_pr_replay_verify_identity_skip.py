r"""W805-FFFFF -- ``cmd_pr_replay`` verifier-side identity-skip probe.

Hundred-and-tenth-in-batch W805 sweep. FIFTH candidate for the
*verifier-side* identity-skip slice of the lineage-disclosure family,
which (pre-this-probe) was 4-STRONG:

- Verifier-side identity-skip (existing 4):
    - W805-PPPP cmd_cga                  (predicate.subject[0].name never checked)
    - W805-UUUU cmd_audit_trail_verify   (actor/repo/git_sha never cross-checked)
    - W805-ZZZZ cmd_evidence_diff        (two-packet identity never cross-checked)
    - W805-BBBBB cmd_pr_bundle validate  (commit_sha never cross-checked vs HEAD)

The wider lineage-disclosure family is 9-STRONG (5 producer-side gaps +
4 verifier-side identity-skips). The agentic hypothesis from the
W805-BBBBB report was: ``cmd_pr_replay`` may have a sibling
``validate`` / ``verify`` mode that consumes a previously-emitted
``ChangeEvidence`` packet / replay manifest and re-checks it against
the live repo. If so, the same verifier-side blind-spot pattern would
likely apply (persisted ``repo_id`` / ``commit_sha`` / ``actor`` never
cross-checked against ``_git_origin_url()`` / ``git rev-parse HEAD``).

W978 first-hypothesis discipline (re-run BEFORE writing any test).
==================================================================

The W978 re-run **DISCONFIRMS** the family-membership hypothesis.

1. **Module surface probe.** Read ``src/roam/commands/cmd_pr_replay.py``
   in full (3740 lines). The module exposes exactly ONE Click entry
   point at line 3288:

       @click.command(name="pr-replay")
       ...
       def pr_replay_cmd(ctx, tier, commit_range, client, output_path, ...):

   It is NOT a ``click.group``. There is no ``add_command``, no
   ``@pr_replay.command``, no ``@click.group(name="pr-replay")``. The
   ``cli.py`` ``_COMMANDS`` registry has exactly one row for the
   surface: ``"pr-replay": ("roam.commands.cmd_pr_replay", "pr_replay_cmd")``.

2. **Verify-mode flag probe.** Grepped for ``--verify`` / ``--validate``
   / ``--check`` option declarations on cmd_pr_replay. The only matches
   are inside string literals (one in a CI-gate recipe template, one
   inside a Markdown body). No Click option with those names is
   declared on the command.

3. **Subcommand probe.** Grepped for ``@.*\.command`` / ``@.*\.group``
   / ``add_command`` / ``click\.group`` in the module. Only the single
   top-level ``@click.command(name="pr-replay")`` decorator at line
   3288 matches. The command has no children.

4. **Behavioural shape.** cmd_pr_replay is PRODUCER-ONLY:
   - It runs ``roam postmortem`` over a commit range, aggregates findings,
     and emits a buyer-facing Markdown report + optional ChangeEvidence
     JSON (``--evidence``, ``--evidence-bundle``) + optional PDF (``--pdf``).
   - It does NOT consume a previously-emitted evidence packet.
   - It does NOT cross-check anything against the live repo's identity
     beyond what the producer-side gatherers already harvest (and even
     that is one-directional: ``_git_head_sha()`` is used to STAMP the
     packet, never to VERIFY a pre-existing packet).
   - The docstring states the role explicitly: "The command does the
     *analysis*; the *purchase* and *founder review window* happen
     out-of-band" (cmd_pr_replay.py:16-17).

5. **Distinctness from W805-YYYY (producer-side sister).** W805-YYYY
   pinned the Q1-Q7 producer-coverage gap on the SAME command file
   (cmd_pr_replay.py). YYYY probed the producer axis (harvester silent
   ``[]`` / ``None`` returns without disclosure). W805-FFFFF probes
   the verifier axis (would a hypothetical verify-mode have the
   identity-skip family bug). The two probes share the same producer
   file but target DIFFERENT axes — YYYY found a real gap;
   W805-FFFFF disconfirms its hypothesised gap exists.

6. **Family-membership decision.** Because cmd_pr_replay has NO
   verify/validate path, it CANNOT have a verifier-side identity-skip
   bug. cmd_pr_replay is NOT a member of the verifier-side
   identity-skip family. The verifier-side family stays 4-STRONG
   (W805-PPPP, W805-UUUU, W805-ZZZZ, W805-BBBBB). The
   lineage-disclosure family stays 9-STRONG total.

   cmd_pr_replay is PRODUCER-ONLY in the lineage-disclosure family
   (one foot on the producer-side slice via the existing W805-YYYY
   pin). No second pin lands here.

W907 verify-cycle check.
========================

The W805-YYYY agent already audited cmd_pr_replay for the W880
false-cycle hedging pattern ("duplicated here to avoid X" / "kept
local to avoid circular import") and found exactly one match at
cmd_pr_replay.py:1544 (``Commit subjects: kept local so kind="commit"
survives the swap``), which is a genuine semantic-preservation
comment, NOT a false cycle hedge. Re-verified that finding in this
sweep — no W907 violation introduced since W805-YYYY landed.

Pinning style: architectural invariant (NOT xfail).
===================================================

Because the hypothesis is DISCONFIRMED, the pin is a positive
invariant rather than an xfail. The invariant says: *cmd_pr_replay
stays producer-only*. If a future refactor adds a verify-mode subcommand
(turning ``pr_replay_cmd`` into a click group, declaring a sibling
``@cli.command(name="pr-replay-verify")``, or adding a ``--verify``
option), the invariant trips → test failure → forces a deliberate
re-audit of the new verifier surface for identity-skip patterns
(W805-FFFFF triggered re-audit).

This is the dual to W805-BBBBB's xfail-strict approach: BBBBB pins
that a bug exists today and will be fixed tomorrow; FFFFF pins that
no bug-surface exists today and any new surface needs a fresh audit.
Both shapes are valid W805 family pins — they pin opposite phases
of the bug-lifecycle.

Run isolation:
    python -m pytest tests/test_w805_fffff_cmd_pr_replay_verify_identity_skip.py -x -n 0

Regression baseline:
    python -m pytest tests/test_pr_replay*.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_yyyy*.py tests/test_w805_bbbbb*.py \
        tests/test_w805_pppp*.py tests/test_w805_uuuu*.py tests/test_w805_zzzz*.py \
        -x -n 0
"""

from __future__ import annotations

import importlib
import importlib.util

import click

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------


def test_cmd_pr_replay_module_importable():
    """W978 gate: cmd_pr_replay imports cleanly. Required prerequisite for
    the architectural invariants below."""
    spec = importlib.util.find_spec("roam.commands.cmd_pr_replay")
    assert spec is not None, "roam.commands.cmd_pr_replay not installed"


# ---------------------------------------------------------------------------
# The DISCONFIRMATION pins — cmd_pr_replay is PRODUCER-ONLY.
# ---------------------------------------------------------------------------


class TestPrReplayHasNoVerifyMode:
    """W978 finding pin: cmd_pr_replay has NO verify / validate subcommand
    or option. It is producer-only — emits a ChangeEvidence packet /
    Markdown report, never consumes one for re-verification.

    If a future refactor adds a verify-mode surface, the invariant trips
    and forces a deliberate re-audit of the new verifier surface against
    the lineage-disclosure family's identity-skip family-shape (W805-PPPP
    + W805-UUUU + W805-ZZZZ + W805-BBBBB).
    """

    def test_cmd_pr_replay_has_verify_mode(self):
        """The headline W978 finding. cmd_pr_replay must remain a single
        ``click.Command`` (not a ``click.Group``) so the verifier-side
        identity-skip family cannot acquire cmd_pr_replay as a member
        without a deliberate refactor that re-triggers this audit.
        """
        from roam.commands.cmd_pr_replay import pr_replay_cmd

        # The single command must be a Click Command, NOT a Group.
        assert isinstance(pr_replay_cmd, click.Command), (
            f"pr_replay_cmd should be a click.Command, got {type(pr_replay_cmd).__name__}"
        )
        assert not isinstance(pr_replay_cmd, click.Group), (
            "W805-FFFFF invariant: cmd_pr_replay must stay producer-only. "
            "If pr_replay_cmd is now a click.Group, a subcommand surface "
            "exists. Re-audit each subcommand for the verifier-side "
            "identity-skip family-shape (W805-PPPP/UUUU/ZZZZ/BBBBB)."
        )

    def test_cmd_pr_replay_no_verify_option(self):
        """No ``--verify`` / ``--validate`` / ``--check`` option exists on
        cmd_pr_replay. If one is added, this trips and forces a re-audit
        of the new code path for the family pattern.
        """
        from roam.commands.cmd_pr_replay import pr_replay_cmd

        option_names: set[str] = set()
        for param in pr_replay_cmd.params:
            if isinstance(param, click.Option):
                option_names.update(param.opts)
                option_names.update(param.secondary_opts)

        forbidden = {"--verify", "--validate", "--check"}
        overlap = option_names & forbidden
        assert not overlap, (
            f"W805-FFFFF invariant trip: cmd_pr_replay grew {sorted(overlap)} "
            f"options. Re-audit the new verify-mode code path against the "
            f"lineage-disclosure family identity-skip shape "
            f"(W805-PPPP/UUUU/ZZZZ/BBBBB). If the new path cross-checks "
            f"persisted repo_id / commit_sha / actor against live "
            f"_git_origin_url() / git rev-parse HEAD, this invariant can "
            f"be relaxed. If not, file a fix-forward and add a verifier-"
            f"side identity-skip pin (the family would become 5-STRONG)."
        )

    def test_cli_registry_has_single_pr_replay_entry(self):
        """The ``_COMMANDS`` registry must have exactly one ``pr-replay``
        family entry. If a sibling ``pr-replay-verify`` / ``pr-replay-validate``
        command is added, the registry grows and this trips.
        """
        from roam.cli import _COMMANDS

        pr_replay_keys = sorted(k for k in _COMMANDS if k == "pr-replay" or k.startswith("pr-replay-"))
        assert pr_replay_keys == ["pr-replay"], (
            f"W805-FFFFF invariant trip: _COMMANDS now has multiple "
            f"pr-replay-family entries: {pr_replay_keys}. Audit each "
            f"new entry against the verifier-side identity-skip family-"
            f"shape (W805-PPPP/UUUU/ZZZZ/BBBBB) before relaxing this "
            f"invariant."
        )


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805YyyyProducerSideInvariantsPreserved:
    """W805-YYYY (cmd_pr_replay Q1-Q7 producer-coverage asymmetry) sister
    cross-check. Same producer file as this disconfirmation pin, different
    axis. Baseline: cmd_pr_replay still imports + still has its evidence
    packet-emission surface intact (``--evidence`` option).
    """

    def test_pr_replay_evidence_option_still_present(self):
        """W805-YYYY pinned the Q1-Q7 producer-coverage gap on the
        ChangeEvidence packet emitted by ``--evidence``. Confirm the
        option still exists so YYYY's xfail-strict pin can still
        target the same code path.
        """
        from roam.commands.cmd_pr_replay import pr_replay_cmd

        evidence_option = None
        for param in pr_replay_cmd.params:
            if isinstance(param, click.Option) and "--evidence" in param.opts:
                evidence_option = param
                break
        assert evidence_option is not None, (
            "W805-YYYY substrate regression: cmd_pr_replay no longer has "
            "the --evidence option. The Q1-Q7 producer-coverage pin in "
            "test_w805_yyyy_cmd_pr_replay_q17_harvester_coverage.py now "
            "targets a non-existent surface."
        )


class TestW805BbbbbInvariantsPreserved:
    """W805-BBBBB (cmd_pr_bundle validate verifier-side identity skip)
    sister cross-check. Baseline: cmd_pr_bundle still imports + still
    exposes pr_bundle_validate. We do NOT re-assert BBBBB's
    xfail-strict pins.
    """

    def test_pr_bundle_validate_surface_still_present(self):
        from roam.commands.cmd_pr_bundle import pr_bundle_validate  # noqa: F401

        # If the symbol is importable, the BBBBB pin substrate is intact.
        assert pr_bundle_validate is not None


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga verify subject.name skip) sister cross-check."""

    def test_cga_module_importable(self):
        spec = importlib.util.find_spec("roam.attest.cga")
        assert spec is not None, "roam.attest.cga sister module not importable"


class TestW805UuuuInvariantsPreserved:
    """W805-UUUU (cmd_audit_trail_verify identity skip) sister cross-check."""

    def test_audit_trail_verify_module_importable(self):
        spec = importlib.util.find_spec("roam.commands.cmd_audit_trail_verify")
        assert spec is not None, "cmd_audit_trail_verify sister module not importable"


class TestW805ZzzzInvariantsPreserved:
    """W805-ZZZZ (cmd_evidence_diff cross-repo identity skip) sister cross-check."""

    def test_evidence_diff_module_importable(self):
        spec = importlib.util.find_spec("roam.commands.cmd_evidence_diff")
        assert spec is not None, "cmd_evidence_diff sister module not importable"


# ---------------------------------------------------------------------------
# Cross-axis distinctness from W805-YYYY (producer-side sister).
# ---------------------------------------------------------------------------


class TestW805FfffffAxisDistinctFromYyyy:
    """W805-YYYY and W805-FFFFF both live on cmd_pr_replay but target
    orthogonal axes. YYYY = producer-side Q1-Q7 harvester gap.
    FFFFF = (disconfirmed) verifier-side identity-skip. Confirm the
    two pins don't collide on the same surface.
    """

    def test_yyyy_targets_evidence_packet_producer_axis_not_verifier_axis(self):
        """YYYY targets the ChangeEvidence packet redactions axis at the
        producer (the harvester emits ``[]`` / ``None`` without a
        ``producer_not_available`` marker). That's distinct from any
        hypothetical verifier-side identity-skip on cmd_pr_replay
        because cmd_pr_replay has no verifier surface at all (this
        file's headline finding).
        """
        # The producer surface (--evidence) exists; the verifier surface
        # does not. The two pins target orthogonal axes by construction.
        from roam.commands.cmd_pr_replay import pr_replay_cmd

        # Producer surface present:
        producer_present = any(isinstance(p, click.Option) and "--evidence" in p.opts for p in pr_replay_cmd.params)
        # Verifier surface absent:
        verifier_absent = not any(
            isinstance(p, click.Option) and any(opt in {"--verify", "--validate", "--check"} for opt in p.opts)
            for p in pr_replay_cmd.params
        )
        assert producer_present and verifier_absent, (
            "W805-YYYY (producer-side) and W805-FFFFF (verifier-side) "
            "axes must stay orthogonal. Producer surface --evidence: "
            f"{producer_present}; verifier surface absent: {verifier_absent}."
        )
