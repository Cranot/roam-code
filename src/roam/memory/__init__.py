"""Repo-local agent memory substrate.

Stores agent memory entries as JSONL at ``.roam/memory.jsonl`` so they
travel with the repo and are portable across agent vendors (Claude /
Cursor / Copilot / human-curated).

This is the SUBSTRATE for R19. Higher-level features (run ledger R20,
constitution R24, etc.) build on top.
"""

from __future__ import annotations

from roam.memory.store import (
    MemoryEntry,
    add_memory,
    list_memory,
    relevant_memory,
)

__all__ = [
    "MemoryEntry",
    "add_memory",
    "list_memory",
    "relevant_memory",
]
