"""Sibling Patch Network v1 — replay-certified defect transfer.

Composes the measured-winner repair-intent reranker (:mod:`repair_scorer`, the
fork-B / T-prime scorer, NOT the graph stack) with the prove-before-trust
replay-gate (:mod:`replay_gate`). Consumed by ``roam sibling-patch apply`` and
gated behind ``ROAM_EXPERIMENTAL_REPAIR_SIBLINGS`` (default-off).
"""

from roam.sibling_patch.repair_scorer import (  # noqa: F401
    DEFECT_KINDS,
    RankedSibling,
    RepairIntent,
    ScorerCandidate,
    derive_repair_intent,
    is_defect_intent,
    parse_patch_changes,
    repair_applicability,
    rerank,
)
from roam.sibling_patch.replay_gate import (  # noqa: F401
    FusionAttestation,
    retarget_patch,
    run_replay_gate,
)
