# Sibling Patch Network v1 (experimental, propose-only, default-OFF)

This experiment ranks places where a proposed repair might also apply, then
optionally checks a candidate using the consumer's own validation command.
The original design was motivated by a private cross-project ranking experiment
(1c, scoped to defect-shaped repairs). Its raw per-cell data is not public here;
that historical result does not establish useful repairs on a new project.

**Status:** originally released in v13.7.0 (2026-07-08). Still
**experimental and propose-only**, gated behind
`ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1` (same flag as `repair-siblings`).
Default-off remains a true no-op: with the flag unset, `sibling-patch` is
absent from the CLI command list, help, the shipped command counts, and the
MCP tool surface (measured — it appears in `roam --help` only with the flag
set). Shipping the code is not the same as endorsing the result: the
cross-org lift is still the open question below.

## What it does

`roam sibling-patch apply <claim.json>` consumes a proof-carrying
`RepairTransferClaim` and, **propose-only**, against *your own* repo:

1. **(a) lexical candidate pool** over your code (roam's own symbol index).
2. **(b) rerank by mined repair-intent** — the measured winner
   (`roam.sibling_patch.repair_scorer`, the fork-B / T-prime scorer; **NOT the
   graph stack**, which transfers poorly cross-org). Deterministic (Rule 10).
   Scoped to defect-shaped intents (deletion/replacement); pure additions are a
   structural no-op.
3. **(c) replay-gate** (`roam.sibling_patch.replay_gate`): run *your own*
   `--validation-command` before and after the candidate patch, in separate
   temporary Git clones from the same committed source. The command must fail
   before and pass after for a green `fusion_attestation`.
4. **(d) propose only** — Roam does not apply the repair to the active checkout,
   commit it, or push it. Replay does create temporary files and execute code;
   propose-only is not a read-only or sandbox guarantee.

```
ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1 roam sibling-patch apply claim.json \
    --validation-command 'pytest -q tests/test_regression.py' --max-replays 3
```

Without `--validation-command` it proposes ranked siblings with replay skipped.
A failing/passing command pair alone does not prove the intended defect was
fixed: inspect the failure, retain valid controls, and use the
[verification evidence guide](concepts/verification-evidence.md).

## Claim validation and trust limits

`roam.knowledge.knowledge_claim` (vendored from `stoa/autopilot/knowledge_claim.py`)
gains an optional `repair_transfer` payload and a **write-time PATCH-FUSION
INVARIANT**: a sibling-detector (locator) record is **inadmissible** without its
declared remedy — a non-empty `candidate_patch` **and** a green
`fusion_attestation` are jointly required. This is structural validation of
supplied fields, not independent authentication of replay or a proven repair.
It rejects a locator without those fields; it does not prevent fabricated
claims or establish the absence of security risk. The consumer never executes an
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

## Upstream integration requires separate review

The roam copy is self-contained so `roam sibling-patch` runs without a stoa
checkout. The original proposal was to mirror these **additions** into the
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

This is an integration checklist, not an instruction to copy changes during a
Roam release. Recheck the upstream implementation, schema ownership, and explicit
deployment scope before any cross-repository change.

## Honest risks / next increment

- **Ranking-lift ≠ landed-fix lift.** The candidate patch may not apply at a
  syntactically-different sibling (`retarget_patch` only rewrites the file path,
  not hunk context); the replay-gate then honestly reports `patch_failed`. Real
  cross-sibling patch synthesis is a NEXT increment.
- **Host access remains.** Replay uses structured arguments, a restricted
  inherited environment, separate HOME/TMP directories, and child-process cleanup.
  It is not a kernel sandbox: repository tests and patched code still run under
  the caller's OS account and may access host paths or the network. Use external
  isolation for hostile code; a temporary clone is not that boundary.
- **THE falsifier for the next increment:** does the lift survive on a **real
  external user's defects** — a stranger runs `sibling-patch apply` against
  their own repo and lands a substantive fix (Rule 9)?
