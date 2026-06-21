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

from dataclasses import dataclass, field
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
    procedure_routes={p: "heavy" for p in CLAUDE_2026_05.procedure_routes},
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


# --- Default selection ---
DEFAULT_PROFILE = "claude-2026-05"


def reset_profile_warnings() -> None:
    """Clear the once-per-profile warning memory.

    Public so tests can assert the fire-once behavior deterministically and
    so batch entry points (e.g. the start of a calibration sweep) can force
    the warning to re-emit after an intentional profile change.
    """
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
    if name not in VALIDATED_PROFILES and name not in _WARNED_PROFILES:
        import sys

        _WARNED_PROFILES.add(name)
        print(  # noqa: T201 — intentional stderr warning from a non-CLI plan helper
            f"warning: calibration profile {name!r} is UNVALIDATED — "
            f"routes are placeholders, not measured. Use one of: "
            f"{list(VALIDATED_PROFILES)}.",
            file=sys.stderr,
        )
    return PROFILES[name]
