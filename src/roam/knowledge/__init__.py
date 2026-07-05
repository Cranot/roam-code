"""Roam knowledge-claim schema (vendored + extended for Sibling Patch Network v1).

This package hosts a self-contained copy of the dormant ``KnowledgeClaim``
registry schema so a consumer can run ``roam sibling-patch apply`` without a
stoa/prakteon checkout. The canonical upstream copy lives at
``stoa/autopilot/knowledge_claim.py``; the SPN v1 additions here (the
``repair_transfer`` payload + the write-time patch-fusion invariant) are the
change to mirror upstream when the owner chooses to deploy.
"""

from roam.knowledge.knowledge_claim import (  # noqa: F401
    KnowledgeClaim,
    KnowledgeRegistry,
    PatchFusionError,
    RepairTransferError,
    validate_repair_transfer,
)
