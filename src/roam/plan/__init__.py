"""roam plan v0 — minimal deterministic task compiler.

Architecture seal anchor: Roam should spend model intelligence only after
local intelligence has reduced the task to its smallest safe shape. v0
ships 7 fields, zero model calls.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# The four public symbols below are re-exported from .compiler. They are
# resolved LAZILY (PEP 562 ``__getattr__``) so that merely importing the
# ``roam.plan`` package does NOT eagerly load the ~12K-line compiler and
# its module globals. Callers that only touch the package pay nothing;
# ``from roam.plan import compile_plan`` still works and pulls the
# compiler in on first access.
if TYPE_CHECKING:
    # For static type checkers / IDEs only — never executed at runtime.
    from .compiler import (
        PlanV0,
        compile_for_artifact,
        compile_plan,
        select_artifact,
    )

__all__ = [
    "PlanV0",
    "compile_plan",
    "select_artifact",
    "compile_for_artifact",
]

# frozenset of public names backed by .compiler; lookup table for __getattr__.
_LAZY_EXPORTS = frozenset(__all__)


def __getattr__(name: str):  # PEP 562
    if name in _LAZY_EXPORTS:
        compiler = importlib.import_module(".compiler", __package__)

        value = getattr(compiler, name)
        # Cache on the module so subsequent lookups skip __getattr__.
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_EXPORTS)
