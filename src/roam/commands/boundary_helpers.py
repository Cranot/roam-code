"""Shared substrate-boundary wrapper helpers for roam commands.

The W607-* family of boundary wrappers all share the same shape: catch a
recoverable exception, append a structured ``<recipe>_<phase>_failed`` marker
to a warnings accumulator, and return a floor default so the command envelope
still composes. This module centralises that mechanism so individual commands
can keep their recipe-specific marker namespaces without copying the wrapper
implementation.
"""

from __future__ import annotations


def make_run_check(recipe_name: str, warnings_out: list[str]):
    """Return a W607-style substrate/aggregation boundary wrapper.

    The returned function has the signature
    ``(phase, fn, *args, default=None, **kwargs)``. On a clean call it returns
    ``fn(*args, **kwargs)``. On any ``Exception`` it appends
    ``{recipe_name}_{phase}_failed:{exc_class}:{exc}`` to *warnings_out* and
    returns *default*.

    This preserves per-recipe marker isolation while removing the duplicated
    try/except boilerplate from every command that implements W607 plumbing.
    """

    def _run_check(phase, fn, *args, default=None, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- top-level boundary disclosure
            warnings_out.append(f"{recipe_name}_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    return _run_check
