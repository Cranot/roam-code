"""Indexing pipeline: discovery, parsing, symbol extraction, and orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roam.index.indexer import Indexer

__all__ = ["Indexer"]


def __getattr__(name: str):
    # Discovery and manifest readers can run without loading the indexer.
    if name == "Indexer":
        from roam.index.indexer import Indexer

        return Indexer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
