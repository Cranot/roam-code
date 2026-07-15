"""Roam: Codebase comprehension tool for AI coding assistants."""

from __future__ import annotations

__all__ = ["__version__"]


def __getattr__(name: str) -> str:
    """Resolve ``roam.__version__`` lazily (PEP 562).

    ``importlib.metadata`` costs ~80 ms to import on Windows, and resolving the
    installed package version at *module import* meant every ``import roam`` —
    and therefore every ``roam`` CLI invocation, including the per-prompt
    compile hook — paid that cost. The compile/hook hot path never reads the
    version, so deferring it here removes ~87 ms from every interactive
    prompt's process startup. The few call sites that DO need the version
    (``roam --version``, SBOM/attest/supply-chain, index-bundle) resolve it on
    first access, exactly as before — ``from roam import __version__`` still
    works.
    """
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("roam-code")
        except PackageNotFoundError:
            return "dev"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
