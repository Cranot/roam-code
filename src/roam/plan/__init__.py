"""roam plan v0 — minimal deterministic task compiler.

Architecture seal anchor: Roam should spend model intelligence only after
local intelligence has reduced the task to its smallest safe shape. v0
ships 7 fields, zero model calls.
"""

from __future__ import annotations

from .compiler import (
    PlanV0,
    compile_for_artifact,
    compile_plan,
    plan_hash,
    select_artifact,
)

__all__ = [
    "PlanV0",
    "compile_plan",
    "plan_hash",
    "select_artifact",
    "compile_for_artifact",
]
