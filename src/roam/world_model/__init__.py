"""World-model detectors: coarse, agent-friendly classification of symbols.

R28 — five sub-features (complete):

- ``side_effects`` classifies symbols by what they DO at runtime
  (``none``, ``io_read``, ``io_write``, ``mutation``, ``process``,
  ``unknown``).  Coarser and more agent-decision-friendly than
  :mod:`roam.analysis.effects` (which has 11 fine-grained kinds and is
  designed for taint/effects propagation).

- ``idempotency`` composes on top of side-effects classification and
  reports whether calling a symbol twice is safe (``idempotent`` /
  ``non_idempotent`` / ``unknown``).

- ``causal_graph`` records per-symbol input -> sink data dependencies
  (which parameter / global / env read flowed into which side-effect).

- ``tx_boundaries`` classifies symbols by transactional shape
  (``transactional`` / ``mutates_outside_tx`` / ``no_mutations`` /
  ``unknown``) so agents can spot mutation-without-rollback hazards.

- ``restore_loss`` flags replace/restore functions that delete tables
  unconditionally but never re-insert some of them, which silently drops
  data during restores.

All detectors are **heuristic** — false negatives are expected (we
miss patterns we haven't enumerated yet), false positives should be
rare (we only mark a kind when call edges or source text contain
clear evidence).  They run in well under 5 seconds on the 18K-symbol
roam-code DB and intentionally do *not* perform AST-deep analysis;
they read directly from the symbols / edges / files tables.

Public surface:

>>> from roam.world_model import (
...     SIDE_EFFECT_KINDS, classify_side_effects,
...     RESTORE_LOSS_KINDS, classify_restore_loss,
...     IDEMPOTENCY_KINDS, classify_idempotency,
...     CAUSAL_KINDS, classify_causal_graph,
...     TX_CLASSIFICATIONS, classify_tx_boundaries,
... )
"""

from __future__ import annotations

from roam.world_model.causal_graph import CAUSAL_KINDS, CausalEdge, CausalGraph, classify_causal_graph
from roam.world_model.idempotency import IDEMPOTENCY_KINDS, IdempotencyClassification, classify_idempotency
from roam.world_model.restore_loss import RESTORE_LOSS_KINDS, RestoreLossFinding, classify_restore_loss
from roam.world_model.side_effects import SIDE_EFFECT_KINDS, SideEffectClassification, classify_side_effects
from roam.world_model.tx_boundaries import TX_CLASSIFICATIONS, TxBoundary, classify_tx_boundaries

__all__ = [
    "SIDE_EFFECT_KINDS",
    "SideEffectClassification",
    "classify_side_effects",
    "RESTORE_LOSS_KINDS",
    "RestoreLossFinding",
    "classify_restore_loss",
    "IDEMPOTENCY_KINDS",
    "IdempotencyClassification",
    "classify_idempotency",
    "CAUSAL_KINDS",
    "CausalEdge",
    "CausalGraph",
    "classify_causal_graph",
    "TX_CLASSIFICATIONS",
    "TxBoundary",
    "classify_tx_boundaries",
]
