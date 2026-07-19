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

import ast

from tests._helpers.repo_root import repo_root


def test_every_command_has_an_explicit_mode_classification():
    """Every registered command has an explicit authority classification.

    The enforcement-completeness audit closed the historical residual surface:
    all pure diagnostics live in ``read_only``, mutating commands have an
    intentional higher tier, and control-plane bootstrap verbs are always
    available. A new command must now be classified in the same change.
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

    assert not unclassified, (
        "registered commands lack mode classification — add each command to "
        "_MODE_EXTRAS in src/roam/modes/policy.py or to "
        f"_MODE_ALWAYS_ALLOWED in src/roam/cli.py: {sorted(unclassified)}"
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
    from roam.modes.policy import _MODE_EXTRAS, VALID_MODES

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
            failures.append(f"mode {mode!r}: missing={missing} extra={extra}")

    assert not failures, (
        "constitution.loader._default_modes() drifted from "
        "policy._MODE_EXTRAS — these MUST stay in lockstep because "
        "the loader treats on-disk constitution mode lists as "
        "REPLACEMENTS (not extras). Drift details:\n" + "\n".join(failures)
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
    from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS, _EXPERIMENTAL_COMMANDS
    from roam.modes.policy import _MODE_EXTRAS

    known = set(_COMMANDS.keys()) | set(_DEPRECATED_COMMANDS.keys()) | set(_EXPERIMENTAL_COMMANDS.keys())
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


def test_registered_side_effect_commands_have_explicit_mode_tiers():
    """Default-on enforcement must never discover a registered write by accident."""
    import importlib

    from roam.capability import REGISTRY
    from roam.cli import _COMMANDS, _MODE_ALWAYS_ALLOWED
    from roam.modes.policy import _MODE_EXTRAS

    classified = set(_MODE_ALWAYS_ALLOWED)
    for commands in _MODE_EXTRAS.values():
        classified.update(commands)

    unclassified: list[str] = []
    for command_name, (module_name, attr_name) in sorted(_COMMANDS.items()):
        command = getattr(importlib.import_module(module_name), attr_name)
        capability = getattr(command, "__roam_capability__", None) or getattr(
            getattr(command, "callback", None), "__roam_capability__", None
        )
        capability = capability or REGISTRY.get(command_name)
        if capability is None:
            continue
        if not (capability.side_effect or capability.destructive):
            continue
        if command_name not in classified:
            unclassified.append(command_name)

    assert not unclassified, (
        "registered side-effect/destructive commands lack an explicit mode "
        f"tier and would fail closed as unknown: {unclassified}"
    )


def test_read_only_max_effect_commands_have_invocation_escalations():
    """A mixed command may stay in read_only only with an audited argv gate."""
    import importlib

    from roam.capability import REGISTRY
    from roam.cli import _MODE_INVOCATION_ESCALATIONS, _command_target
    from roam.modes.policy import _MODE_EXTRAS, VALID_MODES

    missing: list[str] = []
    for command_name in sorted(_MODE_EXTRAS["read_only"]):
        target = _command_target(command_name)
        if target is None:
            continue
        importlib.import_module(target[0])
        capability = REGISTRY.get(command_name)
        if capability is None or not (capability.side_effect or capability.destructive):
            continue
        if command_name not in _MODE_INVOCATION_ESCALATIONS:
            missing.append(command_name)

    assert not missing, (
        f"read_only contains max-effect capabilities without an invocation-level escalation contract: {missing}"
    )
    invalid = {
        command: {trigger: mode for trigger, mode in requirements.items() if mode not in VALID_MODES}
        for command, requirements in _MODE_INVOCATION_ESCALATIONS.items()
        if any(mode not in VALID_MODES for mode in requirements.values())
    }
    assert not invalid


def test_high_confidence_write_options_declare_max_side_effect():
    """Static lint: obvious write flags cannot retain a false max-effect claim."""
    write_options = {
        "--apply",
        "--clear",
        "--emit-guard-findings",
        "--emit-out",
        "--evidence-bundle",
        "--finalize",
        "--import-report",
        "--init-notes",
        "--install",
        "--out",
        "--out-dir",
        "--output",
        "--pdf",
        "--persist",
        "--record",
        "--reset",
        "--sarif-output",
        "--save",
        "--uninstall",
        "--update-baseline",
        "--write",
        "--write-baseline",
    }
    commands_dir = repo_root() / "src" / "roam" / "commands"
    false_claims: dict[str, list[str]] = {}

    for source_path in sorted(commands_dir.glob("cmd_*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        options: set[str] = set()
        side_effect_claims: list[bool] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                name = (
                    decorator.func.attr
                    if isinstance(decorator.func, ast.Attribute)
                    else decorator.func.id
                    if isinstance(decorator.func, ast.Name)
                    else ""
                )
                if name == "option":
                    options.update(
                        argument.value
                        for argument in decorator.args
                        if isinstance(argument, ast.Constant) and argument.value in write_options
                    )
                elif name == "roam_capability":
                    side_effect_claims.append(
                        any(
                            keyword.arg == "side_effect"
                            and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True
                            for keyword in decorator.keywords
                        )
                    )
        if options and side_effect_claims and not any(side_effect_claims):
            false_claims[source_path.name] = sorted(options)

    assert not false_claims, f"write-capable command modules declare side_effect=False: {false_claims}"


def test_newly_audited_write_capabilities_are_pinned():
    """Pin non-obvious DB/process writes that option-name lint cannot infer."""
    import importlib

    from roam.capability import REGISTRY
    from roam.cli import _command_target

    expected = {
        "agent-opt",
        "audit-trail-conformance-check",
        "bench-compile",
        "calc-golden",
        "compile",
        "compile-cache",
        "compile-daemon",
        "compiler-health",
        "coverage-gaps",
        "critique",
        "doctor",
        "envelope-diff",
        "fan",
        "guard-init",
        "health",
        "ingest-trace",
        "laws",
        "mutate",
        "observability-opt",
        "permit",
        "pr-replay",
        "pr-risk",
        "proof-bundle",
        "service-report",
        "tour",
        "vuln-map",
        "vuln-reach",
        "vulns",
        "version",
    }
    false_claims: list[str] = []
    for command_name in sorted(expected):
        target = _command_target(command_name)
        assert target is not None, command_name
        importlib.import_module(target[0])
        capability = REGISTRY.get(command_name)
        if capability is None or capability.side_effect is not True:
            false_claims.append(command_name)
    assert not false_claims, f"audited write capabilities lost max-effect metadata: {false_claims}"


def test_default_policy_side_effect_tiers_are_intentional():
    """Pin the conservative tier chosen for each newly audited effect family."""
    from roam.modes.policy import _CONDITIONAL_MODE_MINIMUMS, _MODE_EXTRAS

    safe_edit = {
        "agents-md",
        "article-12-check",
        "audit-trail-verify",
        "auth-gaps",
        "bench-compile",
        "budget",
        "boundary",
        "bus-factor",
        "capsule",
        "clones",
        "compatibility",
        "compile",
        "complexity",
        "conventions",
        "dark-matter",
        "dead",
        "digest",
        "duplicates",
        "ci-setup",
        "eval-retrieve",
        "evidence-oscal",
        "fitness",
        "fingerprint",
        "fleet",
        "graph-diff",
        "graph-export",
        "hotspots",
        "index-export",
        "ingest-trace",
        "lease",
        "llm-smells",
        "memory",
        "missing-index",
        "n1",
        "orphan-imports",
        "over-fetch",
        "reachability-triage",
        "rules",
        "rules-suggest",
        "savings",
        "savings-backfill",
        "sbom",
        "skill-generate",
        "smells",
        "snapshot",
        "suppress",
        "taint",
        "test-hermeticity",
        "test-scaffold",
        "trend",
        "trends",
        "triage",
        "verify",
        "vibe-check",
        "vuln-map",
        "vuln-reach",
    }
    migration = {"clean", "index-import", "reset", "stale-refs"}
    autonomous_pr = {"dogfood", "hooks", "mcp-setup", "metrics-push", "pre-commit"}

    assert safe_edit <= _MODE_EXTRAS["safe_edit"]
    assert migration <= _MODE_EXTRAS["migration"]
    assert autonomous_pr <= _MODE_EXTRAS["autonomous_pr"]
    assert _CONDITIONAL_MODE_MINIMUMS["sibling-patch"] == "safe_edit"
    assert all("sibling-patch" not in commands for commands in _MODE_EXTRAS.values())
    assert "verify" not in _MODE_EXTRAS["read_only"]
    assert "verify-imports" in _MODE_EXTRAS["read_only"]
    assert "verify-imports" not in _MODE_EXTRAS["autonomous_pr"]
    assert {"compile", "savings", "savings-backfill", "vuln-map", "vuln-reach"}.isdisjoint(_MODE_EXTRAS["read_only"])


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


# ---------------------------------------------------------------------------
# W107 — `findings` and `x-lang` demoted from safe_edit to read_only
# ---------------------------------------------------------------------------


def test_findings_command_is_read_only_mode():
    """W107 — ``findings`` is a pure DB query; it lives in ``read_only``.

    W104 originally added ``findings`` to ``safe_edit`` extras as the
    cheapest classification fix when the mode-classification drift
    catch surfaced it. W106's review flagged this as opinionated:
    all three subcommands (``list``/``show``/``count``) open the DB
    with ``readonly=True`` and never mutate filesystem or DB. The risk
    profile matches ``search`` / ``describe`` / ``fan`` (all already
    ``read_only``), not ``diff`` / ``critique`` / ``pr-bundle`` (the
    actual ``safe_edit`` surface). W107 demotes accordingly.

    The cumulative-inheritance rule means ``findings`` is still
    reachable from every higher mode — the demotion just lets a
    ``read_only`` agent inspect the findings registry without
    upgrading to ``safe_edit``.
    """
    from roam.modes.policy import _MODE_EXTRAS

    assert "findings" in _MODE_EXTRAS["read_only"], (
        "W107: `findings` must live in read_only extras (pure DB query, "
        "no edit semantics). If you moved it back to safe_edit, add a "
        "comment explaining what edit semantics were added."
    )
    assert "findings" not in _MODE_EXTRAS["safe_edit"], (
        "W107: `findings` must NOT also be listed in safe_edit extras — "
        "the cumulative materialisation already lifts it into safe_edit. "
        "Duplicate entries break the assumption that each verb is owned "
        "by exactly one mode."
    )


def test_x_lang_command_is_read_only_mode():
    """W107 — ``x-lang`` is a pure DB query; it lives in ``read_only``.

    ``x-lang`` lists cross-language bridges by reading ``files`` and
    ``symbols`` — purely read-only DB inspection, no FS writes. Same
    reasoning as ``findings`` above: it belongs alongside ``search``
    and ``describe`` at ``read_only``, not at ``safe_edit`` where the
    actual edit-review surfaces live.
    """
    from roam.modes.policy import _MODE_EXTRAS

    assert "x-lang" in _MODE_EXTRAS["read_only"], (
        "W107: `x-lang` must live in read_only extras (pure DB query "
        "listing cross-language bridges). If you moved it back to "
        "safe_edit, add a comment explaining what edit semantics were "
        "added."
    )
    assert "x-lang" not in _MODE_EXTRAS["safe_edit"], (
        "W107: `x-lang` must NOT also be listed in safe_edit extras — "
        "the cumulative materialisation already lifts it into safe_edit."
    )


def test_w107_demotion_preserves_unclassified_ceiling():
    """W107 — moving commands between modes must not change the ceiling.

    W107 demotes ``findings`` and ``x-lang`` from ``safe_edit`` to
    ``read_only``. Both stay CLASSIFIED, just in a different mode.
    The unclassified ceiling must be unchanged. If this test fails,
    the W107 demotion accidentally dropped a verb from ``_MODE_EXTRAS``
    entirely rather than moving it.
    """
    from roam.cli import _COMMANDS, _MODE_ALWAYS_ALLOWED
    from roam.modes.policy import _MODE_EXTRAS

    all_modes_combined: set[str] = set()
    for verbs in _MODE_EXTRAS.values():
        all_modes_combined |= set(verbs)

    unclassified = [cmd for cmd in _COMMANDS if cmd not in _MODE_ALWAYS_ALLOWED and cmd not in all_modes_combined]

    # Same ceiling as ``test_unclassified_command_count_does_not_grow_in_policy``.
    UNCLASSIFIED_CEILING = 152
    assert len(unclassified) <= UNCLASSIFIED_CEILING, (
        f"W107 demotion changed the unclassified count "
        f"({len(unclassified)} > {UNCLASSIFIED_CEILING}). "
        "Did the demotion drop a verb from _MODE_EXTRAS entirely "
        "instead of moving it?"
    )


# ---------------------------------------------------------------------------
# W248 — `ws` (Click group) classified into safe_edit
# ---------------------------------------------------------------------------


def test_ws_command_is_classified():
    """W248 — ``ws`` is reachable from at least one mode's extras.

    Background: W107 left 153 unclassified verbs at the UNCLASSIFIED_CEILING.
    `ws` was the 153rd, so any new command without classification would
    have raised the ceiling and tripped the CI gate. W248 classifies the
    `ws` Click group at the group level (option (a) — most conservative).

    The `ws` group has 7 subcommands; two write to .roam-workspace.json
    and the workspace DB (`ws init`, `ws resolve`), the other five are
    read-only. Group-level safe_edit covers both surfaces because
    cumulative inheritance lifts safe_edit into migration/autonomous_pr.
    """
    from roam.modes.policy import _MODE_EXTRAS

    located_in = [mode for mode, verbs in _MODE_EXTRAS.items() if "ws" in verbs]
    assert located_in, (
        "W248: `ws` must be present in at least one mode's extras. "
        "It is a Click group whose `init` and `resolve` subcommands "
        "write to .roam-workspace.json + workspace DB — classify at "
        "the group level (safe_edit) to cover the strictest subcommand."
    )
    # Pin the chosen tier so a refactor cannot silently re-tier the
    # group (e.g. demoting to read_only would expose ws init/resolve
    # writes at a tier that promises no FS mutation).
    assert "ws" in _MODE_EXTRAS["safe_edit"], (
        "W248: `ws` must live in safe_edit extras specifically — "
        "the group contains `ws init` and `ws resolve` which write "
        "to .roam-workspace.json and the workspace DB. read_only "
        "would be a false promise of no FS mutation."
    )


def test_unclassified_ceiling_decremented_to_152():
    """W248 — UNCLASSIFIED_CEILING must be 152 after classifying `ws`.

    The W248 wave classifies exactly one previously-unclassified verb
    (`ws`), so the ceiling decrements from 153 → 152. This test pins
    the new value so a future change can't silently raise it back.
    """
    from roam.cli import _COMMANDS, _MODE_ALWAYS_ALLOWED
    from roam.modes.policy import _MODE_EXTRAS

    all_modes_combined: set[str] = set()
    for verbs in _MODE_EXTRAS.values():
        all_modes_combined |= set(verbs)

    unclassified = [cmd for cmd in _COMMANDS if cmd not in _MODE_ALWAYS_ALLOWED and cmd not in all_modes_combined]

    assert len(unclassified) <= 152, (
        f"W248: expected ≤152 unclassified commands after classifying "
        f"`ws`, got {len(unclassified)}. Either a new command was added "
        "without classification, or `ws` was dropped from _MODE_EXTRAS."
    )
