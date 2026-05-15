# Control mapping - README

`control-mapping.yaml` maps Roam-produced change evidence (PR bundles, run
ledger events, mode/lease state) to external governance frameworks so that
audit-report renderers can cite the right control next to each piece of
evidence.

## What this file is for

- Drive the "Compliance evidence mapping" section of `sample-audit-report.md`.
- Give downstream evidence-package projections (OSCAL-like JSON/YAML) a stable
  list of control identifiers to render.
- Document, in one place, which Roam fields are required for each control.

It is **not** a compliance certification, and the file's wording (and any
output that uses it) must not claim that running Roam makes a customer
compliant with any named standard.

## Wording discipline

- Use "maps to" or "supports evidence for".
- Use "audit-ready record" when describing the artifact.
- Do **not** use "certifies", "makes compliant", "guaranteed", or any phrase
  that implies a formal attestation. See
  `(internal memo)` (P0.3-P0.4) for the wider rule.

## Current schema: v1 (`control_mapping/v1`)

Each entry in `controls:` carries the following fields. Items marked **NEW
in v1** were added per `(internal memo)`
"Build deltas" item 5.

| Field | Required | Description |
|---|---|---|
| `control_id` | yes | Stable identifier in `ALL_CAPS_SNAKE_CASE`. |
| `source_framework` | yes | Short framework name (e.g. `eu_ai_act_art_12`). Renamed from `standard` in v0. |
| `claim` | yes | Human-readable one-line claim. |
| `required_evidence` | yes | List of dotted refs to Roam evidence fields. |
| `evidence_types` | yes (**NEW v1**) | List of `ChangeEvidence` field categories that satisfy the control (e.g. `actor_refs`, `run_ids`, `audit_trail`). |
| `surface` | yes (**NEW v1**) | List of Roam product surfaces that emit this evidence (e.g. `pr-replay`, `governance-pack`). |
| `wording_guard` | yes (**NEW v1**) | Verbatim discipline phrase the renderer must use - one of `"maps to"`, `"supports evidence for"`, `"audit-ready record"`. |
| `query` | yes | Suggested SQL or `roam --json ...` lookup. |
| `pass_condition` | yes | One of `all_required_present`, `any_required_present`, `conditional`. |
| `export_text` | yes | What the audit report renders for this control. |
| `notes` | optional | Caveats / TODO references. |

### `evidence_types` vocabulary

Drawn from the crosswalk "eight evidence questions" (`AGENTIC-ASSURANCE-CROSSWALK-2026-05-13.md`)
and the `ChangeEvidence` dataclass (`ARCHITECTURE-EVIDENCE-COMPILER-2026-05-13.md`):

- **Actor / authority**: `actor_refs`, `authority_refs`
- **Run / audit**: `run_ids`, `audit_trail`
- **Change scope**: `changed_subjects`, `git_range`, `commit_sha`
- **Decision / risk**: `policy_decisions`, `findings`, `accepted_risks`, `verdict`
- **Tests / approvals**: `tests_required`, `tests_run`, `approvals`

### `surface` vocabulary

Aligned with the seven product surfaces in the crosswalk "Product
implications" section:

- `pr-replay`
- `governance-pack`
- `review`
- `team-mcp-gateway` (deferred)
- `self-hosted` (deferred)
- `due-diligence`
- `security-reachability`

### `wording_guard` rule

Always pick from this closed set (LAW 11 - closed enumeration, not free
composition):

- `"maps to"` - "Roam's X maps to control Y."
- `"supports evidence for"` - "Where the bundle records X, Roam supports evidence for Y."
- `"audit-ready record"` - "X produces an audit-ready record of Y."

A renderer that emits any other phrase is a bug.

## Adding a new control entry

1. Pick a stable `control_id` in `ALL_CAPS_SNAKE_CASE`.
2. Set `source_framework` to one of: `eu_ai_act_art_12`, `iso_iec_42001`,
   `nist_ai_rmf`, `nist_ai_600_1`, `nist_sp_800_218a`, `soc_2_cc8_1`,
   `slsa_src_l2`, `slsa_src_l3`, `internal_ai_change_policy`. Add a new
   value only when another framework is adopted (lockstep with
   `tests/test_doc_consistency.py:_SOURCE_FRAMEWORK_ALLOWED`).
3. Fill `required_evidence` with **dotted refs to fields Roam actually
   produces**. If a field is on the roadmap but not yet emitted, add a
   `notes:` entry that says so explicitly.
4. List `evidence_types[]` from the vocabulary above - typically 2-5 items.
5. List `surface[]` from the vocabulary above - typically 2-4 items;
   `governance-pack` is the default surface for almost every control.
6. Set `wording_guard` from the closed set above so the renderer can match
   the exact phrase in `export_text`.
7. Pick `pass_condition` from `all_required_present`, `any_required_present`,
   or `conditional` (the last when the control applies only to a subset of
   runs - e.g., only when an above-threshold blast radius is present).
8. Write `export_text` using the wording-discipline rules above; the phrase
   in `wording_guard` must appear verbatim in `export_text`.

## Worked example (v1)

```yaml
- control_id: AI_AGENT_RECORD_KEEPING
  source_framework: eu_ai_act_art_12
  claim: Each AI-agent action is recorded with timestamp and actor.
  required_evidence:
    - runs.event.timestamp
    - runs.event.agent
    - runs.event.action
  evidence_types:
    - actor_refs
    - run_ids
    - audit_trail
  surface:
    - governance-pack
    - pr-replay
  wording_guard: "maps to"
  query: |
    SELECT run_id, agent, action, ts
    FROM runs_events
    WHERE run_id = :run_id
    ORDER BY seq;
  pass_condition: all_required_present
  export_text: >-
    Roam's run ledger maps to EU AI Act Article 12 record-keeping by
    capturing timestamp, actor, and action for every recorded agent event.
  notes: |
    Today the ledger is loaded via `roam runs show <run_id>`.
```

## v0 -> v1 migration

The v0 schema (shipped in W175) had 8 per-entry fields. v1 adds 3 and
renames 1.

| v0 field | v1 status |
|---|---|
| `control_id` | unchanged |
| `standard` | **renamed** to `source_framework` |
| `claim` | unchanged |
| `required_evidence` | unchanged |
| (none) | **added**: `evidence_types[]` |
| (none) | **added**: `surface[]` |
| (none) | **added**: `wording_guard` |
| `query` | unchanged |
| `pass_condition` | unchanged |
| `export_text` | unchanged |
| `notes` | unchanged (optional) |

### Rename decision: `standard` -> `source_framework`, clean break

No production code consumed the v0 `standard` field at the time of the
upgrade (verified via repository-wide grep for `control-mapping.yaml`
loaders), so v1 ships as a clean rename rather than a back-compat alias.
The `_vocabulary.py` `artifact_kind="control_mapping"` reference is for
a different layer (artifact kinds in the evidence compiler) and does not
read the YAML schema directly.

If a future loader needs to accept v0 input, it should normalize on read
(`source_framework = entry.get("source_framework") or entry["standard"]`)
rather than reintroducing the alias in this file.

### Previous version: v0 (`control_mapping/v0`)

The v0 contract is preserved for historical reference. It carried these
per-entry fields:

```
control_id, standard, claim, required_evidence, query, pass_condition,
export_text, notes
```

v0 had no `evidence_types[]`, no `surface[]`, and no `wording_guard`. Any
renderer pinned to v0 should be upgraded before the next sprint cycle;
v1 is the source of truth from W184 onward.

## Cross-links

- Architecture: `(internal memo)`
  ("Policy and control mapping" section).
- Crosswalk (drives v1 schema additions):
  `(internal memo)` ("Build deltas" item 5).
- Sample report this powers: `templates/audit-report/sample-audit-report.md`
  (Section 6).
- Evidence extraction commands: `templates/audit-report/evidence-checklist.md`.
