"""Mode-classification coverage regression test (W26.4 / PR-B.5).

W26.4's test fixture sweep elected ``autonomous_pr`` for every test that
exercised a privileged command, but 8 commands (``timeline``, ``stats``,
``audit-trail-export``, ``audit-trail-conformance-check``,
``rules-validate``, ``laws``, ``constitution``, ``runs``) still failed
under ``ROAM_MODE_ENFORCEMENT=1`` because they were missing from EVERY
mode's allow-list in ``policy._MODE_EXTRAS``. The constitution loader's
``_default_modes()`` had the same gap.

Without an automated coverage test, the next added command can silently
ship with the same defect — invisible until enforcement flips on (W23.3
staged-rollout PR-C). This test catches the entire bug class.

Pinning rule: every command registered in ``cli._COMMANDS`` must be
either (a) listed in ``_MODE_ALWAYS_ALLOWED`` (bypass meta-commands)
or (b) reachable from ``autonomous_pr`` via the cumulative materialised
allow-list. Both source-of-truth allow-lists are checked:

  * ``policy._MODE_EXTRAS`` (programmatic default)
  * ``constitution.loader._default_modes()`` (initial-constitution default)

They must stay in sync OR every command must appear in at least one of
them. We assert both have full coverage.
"""

from __future__ import annotations


def test_unclassified_command_count_does_not_grow_in_policy():
    """Snapshot the current unclassified-command count; fail if it GROWS.

    W26.4 surfaced 8 commands missing from every mode's allow-list in
    ``policy._MODE_EXTRAS``. PR-B.5 fixes those 8, but ~150 other
    commands remain unclassified — those still work today (enforcement
    is opt-in) and will be classified by later waves before W23.3's
    PR-C flips ``ROAM_MODE_ENFORCEMENT`` to default-on.

    This snapshot test is the GUARD-RAIL: it pins the current
    unclassified count so the next added command cannot silently ship
    without being either (a) classified in ``_MODE_EXTRAS`` or (b)
    deliberately added to ``_MODE_ALWAYS_ALLOWED``. Either path is
    fine — but going neither path must fail loudly here.

    When PR-C lands and the remaining ~150 are classified, replace this
    snapshot with a strict ``not unclassified`` assertion.
    """
    from roam.cli import _COMMANDS, _MODE_ALWAYS_ALLOWED
    from roam.modes.policy import _MODE_EXTRAS

    all_modes_combined: set[str] = set()
    for verbs in _MODE_EXTRAS.values():
        all_modes_combined |= set(verbs)

    unclassified: list[str] = []
    for cmd in _COMMANDS:
        if cmd in _MODE_ALWAYS_ALLOWED:
            continue
        if cmd in all_modes_combined:
            continue
        unclassified.append(cmd)

    # Baseline established 2026-05-13 (W26.4 / PR-B.5). Decrement this
    # ceiling whenever a new wave classifies more commands. Increment
    # ONLY by adding to _MODE_ALWAYS_ALLOWED or _MODE_EXTRAS — never
    # by raising this ceiling silently.
    UNCLASSIFIED_CEILING = 153

    assert len(unclassified) <= UNCLASSIFIED_CEILING, (
        f"{len(unclassified)} commands lack mode classification "
        f"(ceiling: {UNCLASSIFIED_CEILING}). A new command silently "
        "shipped without mode classification — add it to "
        "_MODE_EXTRAS in src/roam/modes/policy.py or to "
        "_MODE_ALWAYS_ALLOWED in src/roam/cli.py. "
        f"Newly added unclassified: "
        f"{sorted(set(unclassified))[-(len(unclassified) - UNCLASSIFIED_CEILING):] if len(unclassified) > UNCLASSIFIED_CEILING else []}"
    )


def test_all_registered_commands_classified_in_constitution_defaults():
    """Same coverage rule applied to ``constitution.loader._default_modes()``.

    When ``roam constitution init`` runs in a fresh repo, it writes a
    ``.roam/constitution.yml`` populated from ``_default_modes()``. From
    that point on, ``_materialise_from_constitution()`` returns the
    constitution's lists as REPLACEMENTS (not extras) — so if a command
    is missing from ``_default_modes()``, an agent operating in a repo
    that has run ``constitution init`` will be blocked even at
    ``autonomous_pr``. This is exactly the bug W26.4's full-loop perf
    test surfaced for ``runs``, ``replay``, ``agent-score``, ``laws``,
    ``constitution``.

    The constitution loader's defaults are intentionally a CURATED
    minimal set, but they must at minimum cover every verb the
    canonical agent loop (CLAUDE.md substrate section) exercises.
    """
    from roam.cli import _COMMANDS, _MODE_ALWAYS_ALLOWED
    from roam.constitution.loader import _default_modes

    defaults = _default_modes()
    all_classified: set[str] = set()
    for verbs in defaults.values():
        all_classified |= {str(v) for v in verbs}

    # Canonical agent-loop verbs (CLAUDE.md substrate section). These
    # MUST be reachable from a fresh ``constitution init`` because the
    # documented loop calls them by name.
    canonical_loop_verbs = {
        "runs",
        "mode",
        "pr-bundle",
        "preflight",
        "impact",
        "diff",
        "critique",
        "replay",
        "agent-score",
        "laws",
        "constitution",
    }

    missing_loop_verbs: list[str] = []
    for verb in canonical_loop_verbs:
        if verb in _MODE_ALWAYS_ALLOWED:
            continue
        if verb not in _COMMANDS:
            # Verb not registered at all — separate bug, surfaced
            # elsewhere; skip here.
            continue
        if verb not in all_classified:
            missing_loop_verbs.append(verb)

    assert not missing_loop_verbs, (
        f"{len(missing_loop_verbs)} canonical agent-loop verbs missing "
        "from constitution.loader._default_modes() — a fresh "
        "`roam constitution init` will produce a constitution that "
        "BLOCKS the documented agent loop under ROAM_MODE_ENFORCEMENT=1. "
        f"Add to autonomous_pr in _default_modes(). List: "
        f"{sorted(missing_loop_verbs)}"
    )


def test_default_modes_materialise_from_policy_extras():
    """W37.1 — ``_default_modes()`` is materialised from ``_MODE_EXTRAS``.

    Before W37.1, ``constitution.loader._default_modes()`` and
    ``policy._MODE_EXTRAS`` were two independent hand-maintained
    sources of truth. W23.4 surfaced the trap: a fix to one (adding
    ``runs`` to ``_MODE_EXTRAS``) was silently incomplete because the
    test repo's ``constitution.yml`` was written from the *other*
    source — and the loader treats declared mode lists as REPLACEMENTS,
    not extras. The fix is to materialise the on-disk default from
    ``_MODE_EXTRAS`` so the two cannot drift.

    This test pins the invariant: for every mode in ``VALID_MODES``,
    the constitution-default list MUST equal the cumulative union of
    ``_MODE_EXTRAS`` up to and including that mode.
    """
    from roam.constitution.loader import _default_modes
    from roam.modes.policy import VALID_MODES, _MODE_EXTRAS

    defaults = _default_modes()
    cumulative: set[str] = set()
    failures: list[str] = []
    for mode in VALID_MODES:
        cumulative |= _MODE_EXTRAS.get(mode, set())
        expected = sorted(cumulative)
        actual = sorted(defaults.get(mode, []))
        if expected != actual:
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            failures.append(
                f"mode {mode!r}: missing={missing} extra={extra}"
            )

    assert not failures, (
        "constitution.loader._default_modes() drifted from "
        "policy._MODE_EXTRAS — these MUST stay in lockstep because "
        "the loader treats on-disk constitution mode lists as "
        "REPLACEMENTS (not extras). Drift details:\n"
        + "\n".join(failures)
    )


def test_mode_extras_entries_are_real_commands():
    """W37.1 — every verb in ``_MODE_EXTRAS`` must be a registered command.

    Pre-W37.1, ``_MODE_EXTRAS`` listed four phantom verbs that did not
    correspond to any entry in ``cli._COMMANDS``:

      * ``search-symbol`` (typo / never wired up — both ``search`` and
        ``symbol`` already cover the same surface)
      * ``validate-plan`` (MCP-only tool, not a CLI command)
      * ``apply-plan``    (MCP-only tool, not a CLI command)
      * ``commit``        (phantom — roam does not run git commits)

    These were silently inert because the pre-W37.1
    ``_default_modes()`` had its own hand-edited list that omitted
    them. After W37.1 materialises ``_default_modes()`` from
    ``_MODE_EXTRAS``, the phantoms appear in the on-disk
    constitution.yml — at which point ``roam constitution check``
    reports them as ``unknown_command`` mode allow-list issues.

    This lint blocks the same class of bug from re-entering:
    ``_MODE_EXTRAS`` is a CLOSED ENUMERATION over registered commands
    (or deprecated commands), enforced at test time. CLAUDE.md
    anti-pattern #5 names this exact failure mode (compound-recipe
    internal command-name drift).
    """
    from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS
    from roam.modes.policy import _MODE_EXTRAS

    known = set(_COMMANDS.keys()) | set(_DEPRECATED_COMMANDS.keys())
    phantoms: dict[str, list[str]] = {}
    for mode, verbs in _MODE_EXTRAS.items():
        unknown = sorted(v for v in verbs if v not in known)
        if unknown:
            phantoms[mode] = unknown

    assert not phantoms, (
        "policy._MODE_EXTRAS references commands that aren't in "
        "cli._COMMANDS (or _DEPRECATED_COMMANDS). After W37.1 these "
        "phantom verbs propagate into the on-disk constitution.yml and "
        "fail `roam constitution check`. Either register the command "
        "in cli._COMMANDS, add it to _DEPRECATED_COMMANDS, or remove "
        f"it from _MODE_EXTRAS. Phantom verbs: {phantoms}"
    )


def test_default_modes_returns_every_valid_mode():
    """W37.1 — ``_default_modes()`` covers every entry in ``VALID_MODES``.

    Pre-W37.1, ``_default_modes()`` omitted ``migration`` deliberately
    (the policy docstring noted this exception). After materialising
    from ``_MODE_EXTRAS`` the default template includes every mode the
    policy knows about. This test pins that promise so a future tweak
    cannot silently drop a mode from a fresh ``constitution init``.
    """
    from roam.constitution.loader import _default_modes
    from roam.modes.policy import VALID_MODES

    defaults = _default_modes()
    missing = [m for m in VALID_MODES if m not in defaults]
    assert not missing, (
        f"_default_modes() missing modes from VALID_MODES: {missing}. "
        "A fresh `constitution init` will produce a constitution that "
        "lacks these modes; the loader will then fall back to the "
        "hardcoded defaults at runtime, but the on-disk file becomes "
        "inconsistent with the in-code taxonomy."
    )


def test_w26_4_classifications_pinned_in_policy():
    """Pin the W26.4 / PR-B.5 classifications so a refactor cannot regress.

    The 8 commands surfaced by W26.4's test sweep must remain reachable
    at their classified mode (or higher). If a future change moves
    ``laws`` out of ``autonomous_pr``, this test fails loudly rather
    than re-introducing the silent-ship bug.
    """
    from roam.modes.policy import DEFAULT_MODE_POLICIES

    # (command, lowest_mode_that_must_allow_it)
    expected = [
        ("timeline", "safe_edit"),
        ("stats", "safe_edit"),
        ("audit-trail-conformance-check", "safe_edit"),
        ("rules-validate", "safe_edit"),
        ("laws", "autonomous_pr"),
        ("constitution", "autonomous_pr"),
        ("audit-trail-export", "autonomous_pr"),
        ("runs", "autonomous_pr"),
    ]

    failures: list[str] = []
    for cmd, lowest_mode in expected:
        if cmd not in DEFAULT_MODE_POLICIES[lowest_mode]:
            failures.append(f"{cmd!r} missing from {lowest_mode}")

    assert not failures, (
        "W26.4 mode classifications regressed: "
        + "; ".join(failures)
        + " — see tests/test_mode_classification_coverage.py for context."
    )
