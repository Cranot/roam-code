"""Roam knowledge-claim schema (vendored + extended for Sibling Patch Network v1).

This package hosts a self-contained ``KnowledgeClaim`` registry schema so
consumers can run ``roam sibling-patch apply`` independently. The SPN v1
extensions add a ``repair_transfer`` payload and a write-time patch-fusion
invariant.
"""

from __future__ import annotations

from roam.knowledge.knowledge_claim import (  # noqa: F401
    KnowledgeClaim,
    KnowledgeRegistry,
    PatchFusionError,
    RepairTransferError,
    validate_repair_transfer,
)
