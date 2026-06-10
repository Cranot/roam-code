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
# Source: project_all_levers_breakthrough.md, project_x3_haiku_l1_breakthrough.md
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


# --- Default selection ---
DEFAULT_PROFILE = "claude-2026-05"


def list_profiles() -> list[str]:
    """Return all profile names (validated + unvalidated)."""
    return sorted(PROFILES)


def list_validated_profiles() -> list[str]:
    """Return only profiles backed by measured benchmark data."""
    return sorted(VALIDATED_PROFILES)


def get_profile(name: str | None = None) -> CalibrationProfile:
    """Return profile by name; default to the empirically validated one.

    Emits a stderr warning when callers pick a non-validated profile —
    the recommendations won't carry quantitative guarantees.
    """
    if name is None:
        name = DEFAULT_PROFILE
    if name not in PROFILES:
        raise KeyError(f"Unknown calibration profile: {name!r}. Known: {list(PROFILES)}")
    if name not in VALIDATED_PROFILES:
        import sys

        print(  # noqa: T201 — intentional stderr warning from a non-CLI plan helper
            f"warning: calibration profile {name!r} is UNVALIDATED — "
            f"routes are placeholders, not measured. Use one of: "
            f"{list(VALIDATED_PROFILES)}.",
            file=sys.stderr,
        )
    return PROFILES[name]
