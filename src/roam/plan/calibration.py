"""Calibration profiles — separate universal mechanism from model-specific tuning.

The static-compiler MECHANISM is universal (probe-and-fill, classification,
envelope selection, contract specialization). The MODEL CHOICES and COST
RATIOS are calibrations measured against a specific provider/model snapshot.

This module pins the calibration values so:
  1. Swapping models is a profile change, not a code change
  2. Cross-model validation can ship a `gpt-5` or `gemini-2-pro` profile
     without touching `route_for_plan` logic
  3. Re-benchmarking emits a new profile version; old code stays stable
  4. The `compiled_at` + `profile_version` fields become part of every
     routing decision's audit trail

Honest scope: only the Claude profile (`claude-2026-05`) has been empirically
validated by this codebase's benchmarks. Other profiles are placeholders for
future cross-model A/B work.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

ModelTier = Literal["light", "heavy"]

# Conservative default tier for any procedure NOT listed in a profile's
# `procedure_routes`. Heavy (the more capable, costlier model) is deliberate:
# a procedure absent from the table has never been validated for the cheap
# model, so we pay for safety rather than silently downgrade. New classifier
# procedures therefore inherit `heavy` until someone explicitly measures and
# adds a `light` route — the route table is opt-in to cheapness, not opt-out.
# The lint `tests/test_calibration_route_fallback.py` pins which procedures
# currently rely on this fallback so the next addition is an intentional choice.
DEFAULT_TIER: ModelTier = "heavy"


@dataclass(frozen=True)
class RouteTableValidation:
    """Coverage audit for one calibration profile's procedure routing."""

    profile_name: str
    known_procedures: frozenset[str]
    routed_procedures: frozenset[str]
    fallback_acknowledgements: frozenset[str]
    fallback_procedures: frozenset[str]
    phantom_routes: frozenset[str]
    unacknowledged_fallbacks: frozenset[str]
    stale_fallback_acknowledgements: frozenset[str]

    @property
    def ok(self) -> bool:
        """True when every known procedure has a measured route or fallback ack.

        The single aggregate verdict over ALL failure sets — a new failure-set
        field added to this dataclass must be folded into this expression, so
        consumers asserting ``ok`` inherit the new check for free. Deliberately
        test-only-consumed: the route-fallback lint
        (``tests/test_calibration_route_fallback.py``) asserts it as the final
        seal after its per-field assertions. Keep despite no production
        consumers — reviewed 2026-07-02.
        """
        return not (self.phantom_routes or self.unacknowledged_fallbacks or self.stale_fallback_acknowledgements)


@dataclass(frozen=True)
class CalibrationProfile:
    """Pinned routing calibration for a specific provider + model snapshot.

    Empirically validated on a specific date for a specific corpus. Use
    `route_for_plan(..., profile=...)` to apply.
    """

    name: str
    family: Literal["claude", "openai", "google", "open-weight"]
    light_model: str
    heavy_model: str
    # Cost ratios for arithmetic in route rationales. Per-1M-tokens.
    light_input_cost: float
    light_output_cost: float
    heavy_input_cost: float
    heavy_output_cost: float
    # Empirically validated date — for audit + staleness warnings.
    measured_at: str
    measured_corpus: str = "internal-22-task-coding-corpus"
    # Procedure → model tier routing (empirically derived).
    # Default: probe-fired → light; freeform/trace → light; synthesis → heavy.
    procedure_routes: dict[str, ModelTier] = field(default_factory=dict)
    # Confidence bounds — wins observed on validated corpus.
    score_per_dollar_lift_vs_vanilla: float = 0.0
    # Notes worth carrying to consumers of the routing.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def model_for(self, tier: ModelTier) -> str:
        return self.light_model if tier == "light" else self.heavy_model

    def tier_for(self, procedure: str) -> ModelTier:
        """Return the routing tier for a procedure, defaulting to `DEFAULT_TIER`.

        Encapsulates the absent-procedure fallback in one place (instead of a
        bare literal at the call site) so the conservative `heavy` default is
        documented and uniform. A procedure not in `procedure_routes` is routed
        heavy on purpose — see `DEFAULT_TIER`.
        """
        return self.procedure_routes.get(procedure, DEFAULT_TIER)

    def routing_procedures(self) -> frozenset[str]:
        """Procedures this profile has an explicit, measured route for.

        Every key in ``procedure_routes`` — the routed-procedure universe, as
        opposed to the compiler's full ``known_procedures()`` universe. Within a
        profile the two helpers partition the known universe:
        ``routing_procedures()`` and ``unrouted_procedures()`` are disjoint and
        (for routes that are not phantom config) together cover
        ``known_procedures()``.

        Shared (not re-derived as ``set(profile.procedure_routes)`` at each call
        site) so derived profiles and coverage audits reference the routed set by
        name — mirroring how ``known_procedures`` shares the compiler's universe.
        """
        return frozenset(self.procedure_routes)

    def unrouted_procedures(self) -> frozenset[str]:
        """Procedures the compiler knows about that this profile has no route for.

        These inherit ``DEFAULT_TIER`` ("heavy") via ``tier_for`` — the
        conservative fallback for a procedure this profile has never measured a
        cheap route for. Use this to audit profile route coverage: an unexpected
        entry here is a newly-added compiler procedure the profile forgot to
        measure, not a silent downgrade.

        The known-procedure universe is shared from the compiler
        (``roam.plan.compiler.known_procedures``) so the coverage audit stays in
        lockstep with the registry tables instead of being reconstructed at each
        call site. The import is local to avoid a calibration→compiler cycle
        (the compiler already imports calibration lazily inside ``route_for_plan``).
        """
        from roam.plan.compiler import known_procedures  # local import avoids cycle

        return known_procedures() - self.procedure_routes.keys()

    def is_stale(self, today: str) -> bool:
        """Crude staleness heuristic — 90+ days since measurement."""
        # YYYY-MM compare. Conservative.
        try:
            m_y, m_m = self.measured_at.split("-")[:2]
            t_y, t_m = today.split("-")[:2]
            months = (int(t_y) - int(m_y)) * 12 + (int(t_m) - int(m_m))
            return months >= 3
        except (ValueError, IndexError):
            return False


def validate_profile_route_table(
    profile: CalibrationProfile,
    fallback_acknowledgements: Iterable[str],
) -> RouteTableValidation:
    """Validate measured profile routes plus deliberate default-tier fallbacks.

    ``known_procedures()`` is the compiler's procedure universe. A profile must
    either carry an explicit measured route for a known procedure or receive an
    explicit fallback acknowledgement confirming that ``DEFAULT_TIER`` is the
    intended behavior. This keeps heavy-default additions deliberate even when
    the right route-table edit is "leave it unrouted."
    """
    from roam.plan.compiler import known_procedures  # local import avoids cycle

    known = known_procedures()
    routed = profile.routing_procedures()
    fallback_ack = frozenset(fallback_acknowledgements)
    fallback_procedures = known - routed
    return RouteTableValidation(
        profile_name=profile.name,
        known_procedures=known,
        routed_procedures=routed,
        fallback_acknowledgements=fallback_ack,
        fallback_procedures=fallback_procedures,
        phantom_routes=routed - known,
        unacknowledged_fallbacks=fallback_procedures - fallback_ack,
        stale_fallback_acknowledgements=fallback_ack - fallback_procedures,
    )


# --- The default profile: validated this session on Claude 4.x ---
# Source: the compiler lever-inventory notes
CLAUDE_2026_05 = CalibrationProfile(
    name="claude-2026-05",
    family="claude",
    light_model="claude-haiku-4-5",
    heavy_model="claude-sonnet-4-6",
    light_input_cost=1.0,  # USD per 1M tokens (approx, 2026-05)
    light_output_cost=5.0,
    heavy_input_cost=3.0,
    heavy_output_cost=15.0,
    measured_at="2026-05-29",
    measured_corpus="22-task coding benchmark (focus + multirepo)",
    procedure_routes={
        # Probe-fired structural → light (D13: cheapest + right prism wins)
        "structural_coupling": "light",
        "structural_callers": "light",
        "structural_dead": "light",
        "structural_blast": "light",
        "structural_complexity": "light",
        "structural_cycle": "light",
        # Freeform + trace work on light with 3-step+few-shot
        "freeform_explore": "light",
        "trace_query": "light",
        # L1 probe-answer procedures (W11/W12/W13/W28) → light. These are the
        # task-text-only probe families where, when the probe fires, the
        # envelope ALREADY contains the answer — a definition location
        # (W11), a top-N ranking (W12), a pre-computed slow-diagnosis +
        # remediation (W13), or a compare-result (W28). The model's job is to
        # render the embedded result, the same shape as the structural_* light
        # routes above. `route_for_plan` consults this tier only after
        # `_l1_has_procedure_data` returns True, so `light` takes effect ONLY
        # on a successful probe; an empty probe still falls through to the
        # heavy FC-R9 path. Without these explicit routes the four families
        # defaulted to `heavy` (DEFAULT_TIER), paying Sonnet to restate an
        # answer the probe had already computed. See AGENTS.md "Compiler
        # classifier procedures added this wave".
        "symbol_defined_where": "light",
        "top_n_ranking": "light",
        "cli_verb_why_slow": "light",
        "compare_x_vs_y": "light",
        # Synthesis genuinely needs heavy (P120: behavioral reasoning is model-sensitive)
        "synthesis_query": "heavy",
    },
    score_per_dollar_lift_vs_vanilla=2.20,  # +220% on ALL-LEVERS full corpus
    notes=(
        "Routing assumes Claude Agent SDK as runtime — disallowed_tools semantics "
        "and built-in tool list (Read/Grep/Bash) apply.",
        "+220% score/$ measured on 15/22 task subset; classifier extensions "
        "for trace/dead/cycle plural patterns recover the other 7.",
        "Pinned tools: roam-code MCP server; assumes index < 24h old.",
    ),
)


# --- Stub profiles for future cross-model validation ---
GPT_5_2026 = CalibrationProfile(
    name="gpt-5-2026",
    family="openai",
    light_model="gpt-5-mini-2026",  # placeholder
    heavy_model="gpt-5-2026",
    light_input_cost=0.5,
    light_output_cost=2.0,
    heavy_input_cost=5.0,
    heavy_output_cost=20.0,
    measured_at="UNVALIDATED",
    # Defaults: route everything to heavy until measured. P330 says only
    # structural mechanisms cross-port from Claude — magnitudes don't.
    procedure_routes={p: "heavy" for p in CLAUDE_2026_05.routing_procedures()},
    notes=(
        "PLACEHOLDER — never measured against this codebase's benchmark.",
        "Per agi-in-md CP54: cross-model recommendation agreement is poor; "
        "do not transfer claude-2026-05 numbers without re-measuring.",
        "Tool-call semantics may differ — Claude Agent SDK behaviors don't apply.",
    ),
)


# --- Profile registry ---
PROFILES: dict[str, CalibrationProfile] = {
    "claude-2026-05": CLAUDE_2026_05,
    "gpt-5-2026": GPT_5_2026,
}

# Profiles that have actual measurements behind them (W29 — pinned 2026-05-30).
# `get_profile()` raises a warning when callers pick a non-validated profile.
VALIDATED_PROFILES: frozenset[str] = frozenset({"claude-2026-05"})

# Profile names already warned about this process. The warning is
# informational, not an error: `route_for_plan` calls `get_profile` once per
# compile, and calibration sweeps re-emit routes repeatedly for the same
# profile, which would turn a single validation warning into a flood of
# duplicate stderr I/O. Warn once per unvalidated profile instead. Clear via
# `reset_profile_warnings()` (test isolation / batch boundaries).
_WARNED_PROFILES: set[str] = set()
_WARNED_PROFILES_LOCK = Lock()


# --- Default selection ---
DEFAULT_PROFILE = "claude-2026-05"


def reset_profile_warnings() -> None:
    """Clear the once-per-profile warning memory.

    Public so tests can assert the fire-once behavior deterministically and
    so batch entry points (e.g. the start of a calibration sweep) can force
    the warning to re-emit after an intentional profile change.
    """
    with _WARNED_PROFILES_LOCK:
        _WARNED_PROFILES.clear()


def list_profiles() -> list[str]:
    """Return all profile names (validated + unvalidated)."""
    return sorted(PROFILES)


def get_profile(name: str | None = None) -> CalibrationProfile:
    """Return profile by name; default to the empirically validated one.

    Emits a stderr warning when callers pick a non-validated profile —
    the recommendations won't carry quantitative guarantees. The warning
    fires at most once per profile per process (see ``_WARNED_PROFILES``).
    """
    if name is None:
        name = DEFAULT_PROFILE
    if name not in PROFILES:
        raise KeyError(f"Unknown calibration profile: {name!r}. Known: {list(PROFILES)}")
    should_warn = False
    if name not in VALIDATED_PROFILES:
        with _WARNED_PROFILES_LOCK:
            if name not in _WARNED_PROFILES:
                _WARNED_PROFILES.add(name)
                should_warn = True
    if should_warn:
        import sys

        print(  # noqa: T201 — intentional stderr warning from a non-CLI plan helper
            f"warning: calibration profile {name!r} is UNVALIDATED — "
            f"routes are placeholders, not measured. Use one of: "
            f"{list(VALIDATED_PROFILES)}.",
            file=sys.stderr,
        )
    return PROFILES[name]
