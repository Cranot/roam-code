# Sibling Patch Network v1 (experimental, propose-only, default-OFF)

Productizes the measured **1c cross-org WIN** (mined repair-intent reranking
beats a stranger's own grep) — see `fable-packets/SPN_V1_DESIGN.md` and the
falsifier verdict (`SURVIVES, SCOPED` to defect-shaped repairs, judged on
nDCG/P@3/hard-neg, not top-10 recall alone).

**Status:** branch `sibling-patch-network-v1`, gated behind
`ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1` (same flag as `repair-siblings`).
Default-off is a true no-op: the command is absent from the static surface,
help, counts, and MCP until the owner opts in. Nothing here is deployed,
merged, or wired into autopilot.

## What it does

`roam sibling-patch apply <claim.json>` consumes a proof-carrying
`RepairTransferClaim` and, **propose-only**, against *your own* repo:

1. **(a) lexical candidate pool** over your code (roam's own symbol index).
2. **(b) rerank by mined repair-intent** — the measured winner
   (`roam.sibling_patch.repair_scorer`, the fork-B / T-prime scorer; **NOT the
   graph stack**, which transfers poorly cross-org). Deterministic (Rule 10).
   Scoped to defect-shaped intents (deletion/replacement); pure additions are a
   structural no-op.
3. **(c) replay-gate** (`roam.sibling_patch.replay_gate`): in a **throwaway git
   worktree**, run *your own* `--validation-command`, assert it **FIRES**
   pre-patch, apply the candidate patch, assert it **CLEARS** post-patch, and
   localize. Emits a `fusion_attestation`.
4. **(d) propose only** — no push, no write, no commit. Trust never travels;
   the experiment does.

```
ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam sibling-patch apply claim.json \
    --validation-command 'pytest -q tests/test_regression.py' --max-replays 3
```

Without `--validation-command` it proposes ranked siblings but does not certify
(replay is skipped — still fully propose-only).

## The security model = ONE schema invariant (patch-fusion)

`roam.knowledge.knowledge_claim` (vendored from `stoa/autopilot/knowledge_claim.py`)
gains an optional `repair_transfer` payload and a **write-time PATCH-FUSION
INVARIANT**: a sibling-detector (locator) record is **inadmissible** without its
replay-validated remedy — a non-empty `candidate_patch` **and** a green
`fusion_attestation` are jointly required. The locator is inseparable from the
proven fix, which collapses the reverse-fork-B *n-day exploit map* attack (you
cannot publish a bare bug-locator). The consumer additionally never executes an
attacker-supplied command — the replay command is the consumer's *own*
`--validation-command`; the claim's `replay_predicate` is a label, never run.

### `repair_transfer` payload

```json
{
  "repair_intent":     {"kind": "replacement", ...},
  "anchor":            {"file": "...", "symbol": "...", "kind": "function"},
  "candidate_gen":     "lexical_top_n",          // graph is rejected
  "sibling_detector":  "repair_intent_rerank",   // the locator
  "candidate_patch":   "<unified diff>",          // the remedy (required)
  "replay_predicate":  "<validation command>",
  "fusion_attestation": {"status": "green", ...}  // must be green (required)
}
```

## Reuse audit (~80% compose)

- **USE:** the fork-B / T-prime scorer (`repair_applicability` +
  `derive_repair_intent`, the +0.089 winner), the `repair-siblings` lens roam
  integration (`_load_candidate_symbols`, symbol bodies, index), the dormant
  `knowledge_claim.py` registry, lexical candidate-gen.
- **DEMOTE (unused):** the graph sibling detectors W855/856/857 +
  `compare_fingerprints` (1c: graph transfers poorly; no calibrated transfer
  policy).
- **BUILD (new):** the `repair_transfer` payload + patch-fusion validator, the
  replay-gate executor, the `sibling-patch` command.

## Mirror this schema change to stoa at deploy time

The roam copy is self-contained so `roam sibling-patch` runs without a stoa
checkout. When the owner chooses to deploy, mirror these **additions** into the
upstream autopilot copy of `knowledge_claim.py` (identity hash and all existing
behavior are unchanged — `repair_transfer` is payload, deliberately NOT part of
`stable_claim_id`, so no existing claim is re-keyed):

1. Constants: `REPAIR_TRANSFER_CANDIDATE_GENS`, `FUSION_ATTESTATION_STATUSES`,
   `FUSION_GREEN`, `DEFECT_REPAIR_KINDS`.
2. Exceptions: `RepairTransferError`, `PatchFusionError`.
3. Function: `validate_repair_transfer()` (the patch-fusion invariant).
4. `KnowledgeClaim`: the optional `repair_transfer` field threaded through
   `create` / `from_dict` / `to_dict`, and the `if self.repair_transfer is not
   None: validate_repair_transfer(...)` hook at the end of `validate()`.

## Honest risks / next increment

- **Ranking-lift ≠ landed-fix lift.** The candidate patch may not apply at a
  syntactically-different sibling (`retarget_patch` only rewrites the file path,
  not hunk context); the replay-gate then honestly reports `patch_failed`. Real
  cross-sibling patch synthesis is a NEXT increment.
- **Dual-use residual.** The validation command executes in the throwaway
  worktree — propose-only + human-in-the-loop for v1; a diff-safety lens is
  v1.1.
- **THE falsifier for the next increment:** does the lift survive on a **real
  external user's defects** — a stranger runs `sibling-patch apply` against
  their own repo and lands a substantive fix (Rule 9)?
