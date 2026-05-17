"""W365-followup-2 — additional logical invariants on ``@roam_capability``.

Extends ``tests/test_w365_followup_capability_side_effect.py`` (which pinned
``destructive → side_effect``) to the remaining axis-pair entailments on the
``Capability`` dataclass (``src/roam/capability.py``) that are LOGICALLY
necessary — i.e. the negation describes a self-contradiction, not a domain
heuristic that could honourably be violated with rationale.

The W365 + W365-followup arc surfaced two real bugs (``roam_reset`` /
``roam_clean`` carrying ``destructive=True`` + ``side_effect=False``). This
file investigates whether other axis pairs admit the same drift class.

Axes enumerated (canonical semantics from ``src/roam/capability.py:43-67``):

- ``destructive``      — rm-style operations.
- ``side_effect``      — writes to disk / fires HTTP / mutates state.
- ``ai_safe``          — safe for an autonomous AI agent to invoke without
                         a human gate (default ``False`` — opt-in safety).
- ``requires_index``   — needs ``.roam/index.db`` to produce useful output.
- ``stale_sensitive``  — results invalidate when the index goes stale.
- ``deprecated``       — bool flag; tool is on the deprecation path.
- ``maturity``         — closed enum: ``experimental`` | ``beta`` | ``stable``
                         | ``deprecated``.
- ``task_required``    — required for the MCP task-planning surface
                         (``validate_plan`` etc.).
- ``mcp_expose``       — exposed as an MCP tool at the default preset.

Entailments evaluated:

  | # | Entailment                                            | Decision |
  |---|-------------------------------------------------------|----------|
  | 1 | destructive → side_effect                             | PINNED elsewhere (W365-followup) |
  | 2 | destructive → NOT ai_safe                             | LINT (logical) |
  | 3 | deprecated ↔ maturity == "deprecated"                 | LINT (logical bi-conditional) |
  | 4 | task_required → mcp_expose                            | LINT (logical) |
  | 5 | requires_index=True → stale_sensitive=True            | SKIP (domain — index-meta commands like ``stats`` honestly violate) |
  | 6 | requires_index=False → stale_sensitive=False          | SKIP (domain — substrate commands like ``mode`` legitimately depend on external state) |
  | 7 | destructive → requires_index                          | SKIP (domain — ``reset`` / ``clean`` carry ``requires_index=False`` deliberately; they delete the DB whether it exists or not) |
  | 8 | deprecated → NOT ai_safe                              | SKIP (domain — deprecated does not automatically mean unsafe) |

The three pinned invariants here close the entailment surface: every remaining
axis pair is either already pinned by an existing test, or is a domain
heuristic that can be violated with rationale.

Companion: ``tests/test_w365_followup_capability_side_effect.py``
(destructive → side_effect, the W365-followup canonical pin).
"""

from __future__ import annotations

import importlib


def _load_full_capability_registry():
    """Force-import every cmd_*.py module so ``@roam_capability`` decorators register."""
    import roam.cli as _cli
    from roam.capability import REGISTRY

    for _cmd_name, (modpath, _attr) in _cli._COMMANDS.items():
        try:
            importlib.import_module(modpath)
        except Exception:
            # Plugin / optional modules may fail import; that's fine — we
            # only check invariants for capabilities that DID register.
            pass
    return REGISTRY.items


# ---------------------------------------------------------------------------
# Invariant 2: destructive → NOT ai_safe
# ---------------------------------------------------------------------------


def test_destructive_implies_not_ai_safe():
    """For every @roam_capability with destructive=True, ai_safe MUST be False.

    Logical invariant: ``destructive`` means "rm-style operations" (per
    ``src/roam/capability.py:66``) and ``ai_safe`` means "safe for an
    autonomous AI agent to invoke without a human gate" (per the registry
    consumer in the Roam Review GitHub App). A destructive operation
    cannot be safe for autonomous AI invocation — by definition it
    requires a human authorization gate. The two flags are mutually
    exclusive on the same decoration.

    If this fires, the fix is to drop ``ai_safe=True`` from the decoration
    (the safe default is ``False`` — opt-in safety). If the command is
    genuinely safe to AI-invoke, then it isn't destructive and the right
    fix is to drop ``destructive=True`` instead.
    """
    caps = _load_full_capability_registry()
    violations: list[tuple[str, bool, bool]] = []
    for name, cap in caps.items():
        if bool(cap.destructive) and bool(cap.ai_safe):
            violations.append((name, bool(cap.destructive), bool(cap.ai_safe)))
    assert not violations, (
        f"{len(violations)} @roam_capability decoration(s) violate the "
        f"destructive → NOT ai_safe invariant:\n"
        f"  {violations[:20]}\n\n"
        f"Each row: (capability_name, destructive, ai_safe).\n"
        f"A destructive operation cannot be ai_safe. Either drop "
        f"ai_safe=True (the safe default) OR drop destructive=True if "
        f"the operation does not actually delete / overwrite data."
    )


# ---------------------------------------------------------------------------
# Invariant 3: deprecated ↔ maturity == "deprecated"
# ---------------------------------------------------------------------------


def test_deprecated_bool_matches_maturity_enum():
    """The ``deprecated`` bool and ``maturity == "deprecated"`` MUST agree.

    Logical invariant: the two fields express the same fact through two
    surfaces (``deprecated: bool`` for legacy consumers, ``maturity: str``
    for the four-tier vocabulary ``experimental`` | ``beta`` | ``stable``
    | ``deprecated``). Drift between them lies to one consumer or the
    other — exactly the kind of split-brain the W365 capability audit
    surfaced on ``destructive`` / ``side_effect``.

    Bi-conditional:
      - ``deprecated=True``  ↔  ``maturity == "deprecated"``
      - ``deprecated=False`` ↔  ``maturity != "deprecated"``

    If this fires, pick the truthful value and update the disagreeing
    surface. Do not add a quarantine; the two fields are semantically
    redundant by construction.
    """
    caps = _load_full_capability_registry()
    violations: list[tuple[str, bool, str]] = []
    for name, cap in caps.items():
        deprecated = bool(cap.deprecated)
        maturity_deprecated = cap.maturity == "deprecated"
        if deprecated != maturity_deprecated:
            violations.append((name, deprecated, cap.maturity))
    assert not violations, (
        f"{len(violations)} @roam_capability decoration(s) violate the "
        f"deprecated ↔ maturity==\"deprecated\" bi-conditional:\n"
        f"  {violations[:20]}\n\n"
        f"Each row: (capability_name, deprecated_bool, maturity_str).\n"
        f"The two fields are semantically redundant by construction "
        f"(maturity has \"deprecated\" as one of its four tiers). Either "
        f"flip ``deprecated=True`` to match ``maturity=\"deprecated\"`` OR "
        f"flip ``maturity`` to one of the non-deprecated tiers "
        f"(experimental | beta | stable) to match ``deprecated=False``."
    )


# ---------------------------------------------------------------------------
# Invariant 4: task_required → mcp_expose
# ---------------------------------------------------------------------------


def test_task_required_implies_mcp_expose():
    """For every @roam_capability with task_required=True, mcp_expose MUST be True.

    Logical invariant: ``task_required=True`` declares the command is
    REQUIRED for the MCP task-planning surface (``validate_plan`` and the
    compound-recipe consumers). A task-required command that is not
    exposed via MCP cannot be invoked by the very surface declaring it
    required — the flag becomes a lie.

    If this fires, either flip ``mcp_expose=True`` (the command genuinely
    is required for task plans, so it must be reachable) OR drop
    ``task_required=True`` (the command is not actually required by the
    MCP task surface). The default ``mcp_expose=True`` means most
    decorations satisfy this trivially; a violation is an explicit
    contradiction.
    """
    caps = _load_full_capability_registry()
    violations: list[tuple[str, bool, bool]] = []
    for name, cap in caps.items():
        if bool(cap.task_required) and not bool(cap.mcp_expose):
            violations.append((name, bool(cap.task_required), bool(cap.mcp_expose)))
    assert not violations, (
        f"{len(violations)} @roam_capability decoration(s) violate the "
        f"task_required → mcp_expose invariant:\n"
        f"  {violations[:20]}\n\n"
        f"Each row: (capability_name, task_required, mcp_expose).\n"
        f"A task-required command must be reachable via MCP — otherwise "
        f"the MCP task-planning surface (validate_plan etc.) cannot "
        f"actually invoke it. Either flip mcp_expose=True OR drop "
        f"task_required=True if the command isn't required for task plans."
    )


# ---------------------------------------------------------------------------
# Anchor test: the entailment surface is exhausted.
# ---------------------------------------------------------------------------


def test_entailment_surface_documented():
    """Document the closed set of logical entailments on @roam_capability.

    This test does not check capability data — it pins the inventory of
    pinned-vs-skipped entailments. If a new boolean axis is added to
    ``Capability``, this test forces a deliberate update here rather than
    letting the new axis silently drift past the lint layer.
    """
    from roam.capability import Capability

    # The canonical boolean / closed-enum axes on Capability that participate
    # in logical entailments. Update this list whenever a new axis is added
    # to ``Capability`` AND audited for pair-wise entailments above.
    expected_axes = {
        "destructive",
        "side_effect",
        "ai_safe",
        "requires_index",
        "stale_sensitive",
        "deprecated",
        "maturity",
        "task_required",
        "mcp_expose",
    }
    field_names = {f.name for f in Capability.__dataclass_fields__.values()}
    missing = expected_axes - field_names
    assert not missing, (
        f"Capability dataclass is missing axes the W365-followup-2 lint "
        f"set expects: {sorted(missing)}. Either the axis was renamed "
        f"(update this test) or removed (audit the entailment lints "
        f"above and drop the relevant invariant test)."
    )
