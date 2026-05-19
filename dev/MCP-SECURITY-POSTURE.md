# MCP Security Posture

**Audience.** Gateway / Policy-Enforcement-Point (PEP) developers integrating
roam-code into a multi-server MCP fleet — Interlock, Lasso, Portkey,
MintMCP, Operant, MCP Manager, and similar. This document is a technical
specification and integration contract, not marketing copy.

**Status.** Companion to the public reply on Discussion #37 and the
`#security-stance` section in `templates/distribution/landing-page/docs/mcp-usage.html`.
This is the schema-stable, integration-grade version.

**Last updated.** 2026-05-19.

---

## 2026-05-18 wave summary

Same-day closure of the P0 + P1 + P2 frontier the day-of memos had flagged
as in-flight. Six items shipped; one public reply posted.

- **MCP-P0.1** SHIPPED — egress secret redaction (closed-enum
  `redactions=("secret",)` + per-pattern detail).
- **MCP-P0.2** SHIPPED — MCP-boundary mode gate (`policy_decision`
  closed-enum from the live policy substrate).
- **MCP-P0.3** SHIPPED — receipt sha256 anchored in the signed ledger;
  `verify_chain_with_receipts` adds the `receipt_integrity` closed enum.
- **MCP-P1.1** SHIPPED — shadow-mode env flag (`ROAM_MODE_DRY_RUN`)
  emitting `policy_decision=would_deny_dry_run` for observe-only rollout.
- **MCP-P2.1** SHIPPED — this document.
- **MCP-P2.2** SHIPPED — portable JSON Schema (Draft 2020-12) export
  via `scripts/export_mcp_receipt_schema.py`.
- **Discussion #37** — public reply posted at
  https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163.

---

## Table of contents

1. [TL;DR — where roam draws the line](#tldr--where-roam-draws-the-line)
2. [The five controls](#the-five-controls)
3. [What roam emits](#what-roam-emits)
4. [What roam does NOT do](#what-roam-does-not-do)
5. [Integration shape](#integration-shape)
6. [Schema-stability commitment](#schema-stability-commitment)
7. [Roadmap](#roadmap)

---

## TL;DR — where roam draws the line

The MCP runtime-security stack splits across four tiers. roam-code owns one
of them and emits evidence the others can read.

1. **Spec** owns identity, scope-consent, and the `_meta` envelope on
   tool-call results.
2. **Host** (Claude Desktop, IDE plugin, custom MCP client) owns interactive
   approval — the human-in-the-loop "allow this tool call?" prompt.
3. **Server** (roam-code) owns coarse read-only / write flags on every tool,
   scope-based tool filtering via the 4-mode policy substrate, integrity of
   the tool descriptions returned to the host, and structured-evidence
   emission (decision receipts + HMAC-chained run ledger).
4. **Gateway** (Interlock, Lasso, Portkey, ...) owns cross-server policy,
   audit aggregation across multiple MCP servers, shadow-mode rollout,
   response-content scanning, and tenant isolation.

roam intentionally does NOT try to be a gateway. The receipt + ledger
streams are designed to be tailed by a PEP, not replaced by one.

---

## The five controls

Five widely-cited MCP runtime-security controls, mapped onto the four tiers.
"Owns" means "produces the authoritative artifact for that control"; lower
tiers can still emit hints, but the named tier is where the final decision
lives.

| Control                       | Spec | Host | Server (roam)             | Gateway                   |
| ----------------------------- | ---- | ---- | ------------------------- | ------------------------- |
| 1. Argument inspection        |      |      | structural (coarse flags) | semantic policy           |
| 2. Per-role permissions       |      |      | **owns** (4-mode policy)  | cross-server coordination |
| 3. Audit logs                 |      |      | **owns** (HMAC-anchored receipts + run ledger) | aggregation + retention   |
| 4. Shadow / dry-run           |      |      | structural (`ROAM_MODE_DRY_RUN`, MCP-P1.1 shipped 2026-05-18) | **owns** (cross-server) |
| 5. Response content scanning  |      |      | structural (regex secret) | **owns** (semantic)       |

Reading guide:

- **Argument inspection.** roam declares per-tool `read_only` /
  `destructive` / `idempotent` flags in `_TOOL_METADATA` and surfaces them
  on every receipt as `declared_side_effects`. A gateway can reject calls
  whose declared side effects exceed the caller's authority *before* the
  call lands at the server. Semantic argument inspection (e.g. "this
  `path` argument looks like SSRF") is a gateway concern.
- **Per-role permissions.** roam owns this on the in-server axis through
  the 4-mode policy substrate (`read_only` / `safe_edit` / `migration` /
  `autonomous_pr`). MCP-P0.2 (shipped 2026-05-18) wires
  `_evaluate_mcp_mode_policy` + `_build_mode_blocked_envelope` into the
  MCP boundary, so `policy_decision` on the receipt is now a closed enum
  drawn from `_POLICY_DECISIONS` — the 6-member receipt-tier subset
  (`allow` / `deny` / `escalate` / `redact` / `not_evaluated` /
  `would_deny_dry_run`) of the 9-member canonical
  `POLICY_DECISIONS` vocabulary — reflecting an actual enforcement
  decision rather than a hard-coded allow. A gateway can map external
  roles to roam modes and pass the resolved mode in as `ROAM_AGENT_MODE`
  per tool call.
- **Audit logs.** roam owns the per-tool decision receipt and the
  HMAC-chained run ledger. As of MCP-P0.3 (shipped 2026-05-18), each
  receipt's sha256 content hash is also linked into a signed ledger
  event, so receipt tampering is detectable offline via
  `verify_chain_with_receipts` (extends the 4-state `roam runs verify`
  envelope with a `receipt_integrity` closed enum: `ok` / `missing` /
  `tampered` / `not_linked`). A gateway aggregates across multiple
  servers, applies retention, and forwards to SIEM. roam does not aggregate.
- **Shadow / dry-run.** Roam's shadow-mode `ROAM_MODE_DRY_RUN` flag
  shipped 2026-05-18 as MCP-P1.1 (`src/roam/mcp_server.py` policy gate).
  Setting `ROAM_MODE_DRY_RUN=1` flips the mode gate to observe-only:
  the policy evaluates as it would in steady-state but emits
  `policy_decision=would_deny_dry_run` instead of blocking, and the
  registry records the finding so an auditor can see what WOULD have
  been denied. Gateways still own cross-server shadow rollout; roam
  owns the in-server flag.
- **Response content scanning.** roam ships structural regex-based secret
  redaction on egress (MCP-P0.1, shipped 2026-05-18) via
  `redact_secrets_in_string` + `redact_secrets_in_value` at
  `_wrap_with_receipt`, surfacing through the closed-enum
  `redactions=("secret",)` on every affected receipt. Semantic
  content-scanning (PII inference, prompt-injection marker detection,
  model-aware policy) is a gateway concern. MCP-P1.2 will add a coarse
  prompt-injection marker scan at the server boundary.

---

## What roam emits

Three artifact streams a gateway can consume. All are local-filesystem,
zero-network, and stable enough to integrate against today.

### 3.1 `McpDecisionReceipt` — per-tool-call decision receipt

Authoritative source: `src/roam/evidence/mcp_receipt.py`. One JSON file per
sensitive tool call. Frozen dataclass; deterministic JSON serialisation via
`to_canonical_json()`; stable sha256 content hash via
`compute_content_hash()`.

Fields:

| Field                    | Type                  | Notes                                                                       |
| ------------------------ | --------------------- | --------------------------------------------------------------------------- |
| `tool_call`              | `str`                 | Opaque per-invocation id (`<tool>_<12-hex>`).                               |
| `client_id`              | `str`                 | MCP client process id from `ROAM_MCP_CLIENT_ID` env var.                    |
| `tool_name`              | `str`                 | Canonical tool name (e.g. `roam_preflight`).                                |
| `actor_ref_id`           | `str \| None`         | Agent id from `ROAM_AGENT_ID`; ties to W182 `ActorRef.actor_id`.            |
| `declared_side_effects`  | `tuple[str, ...]`     | E.g. `("read_only",)`, `("write_filesystem",)`. From `_TOOL_METADATA`.      |
| `required_mode`          | `str \| None`         | `read_only` / `safe_edit` / `migration` / `autonomous_pr`.                  |
| `input_hash`             | `str \| None`         | sha256 of canonical-JSON input args. Never the args themselves.             |
| `policy_decision`        | `str`                 | Closed enum from 6-member `_POLICY_DECISIONS`: `allow` / `deny` / `escalate` / `redact` / `not_evaluated` / `would_deny_dry_run`. |
| `output_ref`             | `str \| None`         | Artifact id when output is large. Mutually exclusive with `output_hash`.    |
| `output_hash`            | `str \| None`         | sha256 of inline output when small. Mutually exclusive with `output_ref`.   |
| `run_event_id`           | `str \| None`         | Link to `.roam/runs/<id>/events.jsonl` row.                                 |
| `redactions`             | `tuple[str, ...]`     | Closed enum (see below). Stable across versions.                            |
| `extra`                  | `Mapping[str, Any]`   | Free-form structured detail. Includes `redaction_details`.                  |

**The `redactions` closed enum** is the canonical W226
`REDACTION_REASONS` vocabulary from `src/roam/evidence/_vocabulary.py`:

```
secret
pii
sensitive_content
size_limit
policy
user_opt_in_required
machine_local_path
schema_strict
producer_not_available
```

Membership is validated at receipt construction; unknown reasons raise
`ValueError`. Today (2026-05-18) the only reason emitted by the MCP
egress path is `secret` — the structural regex scan in
`src/roam/security/redact.py` covers GitHub PAT (classic + fine-grained),
OpenAI/Anthropic `sk-` keys, AWS AKIA, Bearer tokens, PEM private-key
markers, and JWT. Other reasons are reserved for producer paths
that already populate them (`pii`, `machine_local_path`, etc. — see
`evidence/collector.py`).

**Per-pattern detail** rides in `extra["redaction_details"]` as a
`{pattern_id: hit_count}` map. The closed-enum invariant on `redactions`
holds; the detail is structured but unconstrained. Example receipt
fragment after MCP-P0.1 (shipped 2026-05-18):

```json
{
  "redactions": ["secret"],
  "extra": {
    "redaction_details": {"github_pat_classic": 2, "aws_akia": 1}
  }
}
```

**Storage layout.** One file per call at
`.roam/mcp_receipts/<run_id>/<tool_call>.json`. When no active run, the
bucket is `_no_run`. Atomically written via `atomic_write_text`.

Receipt's sha256 is now linked into the HMAC chain — verify with
`roam runs verify` for tamper-evident proof.

**Receipt-to-chain anchoring (MCP-P0.3, shipped 2026-05-18).** Each
receipt's sha256 hex content hash now appears as a `receipt_hash` field
on a signed ledger event in `.roam/runs/<run_id>/events.jsonl`. The
helper `verify_chain_with_receipts()` in `src/roam/runs/signing.py`
(lines 414-518; closed-enum `RECEIPT_INTEGRITY_STATES` declared at
lines 394-401) extends the offline 4-state envelope with a
`receipt_integrity` closed enum:

| Value         | Meaning                                                                |
| ------------- | ---------------------------------------------------------------------- |
| `ok`          | Every ledger-linked receipt's on-disk sha256 matches the linked hash.  |
| `missing`     | A ledger event names a receipt file that is no longer on disk.         |
| `tampered`    | A receipt file on disk no longer hashes to the value the chain anchors.|
| `not_linked`  | Receipts exist on disk that no ledger event anchors (pre-P0.3 buckets).|

Hash-stability promise: pre-P0.3 chains hash byte-identical to before
(no migration needed). New tests live at
`tests/test_w_mcp_receipt_hmac_link.py` (9 passing).

### 3.2 HMAC-chained run ledger

Authoritative source: `src/roam/runs/ledger.py` + `src/roam/runs/signing.py`.
One run is one directory at `.roam/runs/<run_id>/`. Two files: `meta.json`
(run identity, start/end timestamps, agent id, mode) and `events.jsonl`
(append-only, one event per line). Events carry a chained sha256 + HMAC
signature.

**Offline verification.** `roam runs verify <run_id>` returns one of four
states:

| State       | Meaning                                                                 |
| ----------- | ----------------------------------------------------------------------- |
| `ok`        | Chain intact; every signed event verifies under the active HMAC key.    |
| `tampered`  | At least one event fails verification or a signed run goes unsigned mid-stream. The `first_tamper_at_seq` field names the first failing event. |
| `unsigned`  | The whole chain has no signatures (advisory, not failure).              |
| empty       | Zero events in the ledger.                                              |

The `first_tamper_at_seq` field on a `tampered` result enables targeted
triage. A signed run that goes unsigned mid-stream is reported as
`tampered`, not `unsigned` — silently dropping signatures cannot pass
verification. See the docstring in `src/roam/runs/signing.py` for the
full state machine.

Gateways should treat `tampered` as a hard fail and `unsigned` as a
policy decision (some deployments deliberately run without signing).

### 3.3 Mode policy substrate

Authoritative source: `src/roam/modes/policy.py`. Four cumulative modes:

| Mode             | Adds                                                          |
| ---------------- | ------------------------------------------------------------- |
| `read_only`      | search, retrieve, context, understand, impact, preflight, ... |
| `safe_edit`      | + diff, critique, pr-bundle, annotate, plan, ...              |
| `migration`      | + migration-plan, migration-safety, simulate, mutate, ...     |
| `autonomous_pr`  | + pr-prep, attest, verify, cga, agent-plan, runs, ...         |

Resolution priority (highest wins): explicit `--mode` flag → `ROAM_AGENT_MODE`
env var → `.roam/active_mode` file → default `safe_edit`. Constitution at
`.roam/constitution.yml` can override the default per-mode allow-lists.

**Mode-gate enforcement (MCP-P0.2, shipped 2026-05-18).** Historically
the mode gate was enforced only on the CLI path via `_enforce_mode_gate`
at `cli.py`, and MCP wrappers bypassed it via `_run_roam_inprocess`.
MCP-P0.2 wires `_evaluate_mcp_mode_policy` + `_build_mode_blocked_envelope`
into `mcp_server.py`, so the receipt's `policy_decision` is now a
closed-enum decision from `{allow, deny, not_evaluated}` reflecting an
actual mode-gate check at the MCP boundary. Gateways can read
`policy_decision` today as proof of an enforcement decision; the legacy
hard-coded `"allow"` no longer applies on the MCP path.

---

## What roam does NOT do

Honest list. If you need any of these, the gateway is the right place.

- **No prompt-injection marker scanning today.** Queued as MCP-P1.2. The
  egress redaction layer only scans for structural secret patterns, not
  for `|im_end|` smuggling, `ignore previous instructions` payloads,
  `system:` prefix smuggling, BOM smuggling, or base64-encoded common
  payloads. A gateway with a model-aware content scanner stays
  authoritative on this axis.
- **No cross-server shadow-mode coordination.** The in-server flag
  (`ROAM_MODE_DRY_RUN`) shipped 2026-05-18 as MCP-P1.1 and lets one
  roam server preview enforcement locally. Coordinating shadow rollout
  across a fleet — staged percentage rollout, per-tenant flips, A/B
  observation — stays a gateway concern.
- **No cross-server correlation.** Receipts are per-tool, per-run, per-server.
  Aggregating across multiple MCP servers in a fleet — tying one user's
  receipts on roam to their receipts on a different MCP server — is a
  gateway concern. roam does not emit a fleet-correlation id.
- **No model-aware semantic content scanning.** The egress redaction layer
  is purely structural (regex secret patterns from
  `src/roam/security/redact.py`). It cannot detect "this output contains a
  PII inference the model derived from public data" or "this output
  encodes the system prompt." Those are gateway concerns.
- **No external token issuance, revocation, or rotation.** roam consumes
  the agent identity it is told about via `ROAM_AGENT_ID` /
  `ROAM_MCP_CLIENT_ID`. It does not mint or validate tokens. Identity
  proofing belongs to the host or a separate identity provider.
- **No tenant isolation.** roam is a single-tenant local server. A
  gateway fronting multiple tenants is responsible for namespacing
  `.roam/` state per tenant.
- **No network-shaped policy.** roam does not block egress traffic, rate-limit,
  or enforce per-endpoint quotas. Gateways with network-policy substrate
  (Operant, MintMCP) stay authoritative on this axis.
- **No automatic remediation.** roam emits evidence; it does not roll
  back a tool call after the fact, quarantine an agent, or trigger a
  gateway reconfiguration. Those side effects are the consumer's
  responsibility.

---

## Integration shape

The recommended gateway-PEP integration pattern is **stream-tailing**, not
re-derivation. Per the canonical mandate in the roam architecture
(`CLAUDE.md` — "every exporter is a projection from shared evidence, not
a second source of truth"), a PEP should NOT re-derive receipts from the
raw graph or shadow-implement what `McpDecisionReceipt` already captures.

**Recommended pattern.**

1. Tail `.roam/mcp_receipts/<run_id>/*.json` for new files. Each file is a
   complete, atomically-written `McpDecisionReceipt` JSON.
2. Tail `.roam/runs/<run_id>/events.jsonl` for the signed event stream that
   anchors each tool call to a run identity.
3. Verify the chain on a cadence appropriate to the deployment (`roam runs
   verify --json` returns the 4-state envelope).
4. Apply gateway-side policy (cross-server correlation, semantic content
   scanning, shadow mode, tenant isolation) using the receipt as primary
   evidence.
5. Forward the receipt + chain-verification result to SIEM / GRC tooling
   downstream. Use `McpDecisionReceipt.compute_content_hash()` as a stable
   id for deduplication.

**File-watching mechanics.** `.roam/mcp_receipts/<run_id>/` accumulates
files monotonically within a run; the run id changes at run-start. A
simple inotify / ReadDirectoryChangesW watcher on the bucket directory is
sufficient. Atomic-write via `os.replace` guarantees no torn JSON.

**Identity passing.** The gateway should set two env vars on every MCP
tool-call spawn:

- `ROAM_AGENT_ID` — the resolved agent identity (e.g. `agent_anthropic_claude_1`).
- `ROAM_MCP_CLIENT_ID` — the MCP client identity (e.g. `claude_desktop_v0.5.0`).

Receipts carry both fields. Without them, receipts fall back to `<unknown>`
for `client_id` and `None` for `actor_ref_id`, which still validates but
loses the identity axis of the agentic-assurance crosswalk.

**Mode passing.** Set `ROAM_AGENT_MODE` to the resolved mode for the
current caller. The mode substrate resolves env var ahead of the on-disk
sticky mode, so gateway-supplied modes win deterministically per call.

**What NOT to do.**

- Do not query roam's SQLite index directly to reconstruct receipts. The
  receipt is the authoritative artifact.
- Do not assume the receipt schema is open. Closed-enum fields
  (`redactions`, `policy_decision`) reject unknown values at the producer
  side; gateways adding their own reason strings should land them
  upstream in `REDACTION_REASONS` first.
- Do not assume the receipt file is the sole audit artifact. As of
  MCP-P0.3 (shipped 2026-05-18), receipts are anchored into the
  HMAC-chained run ledger by sha256. Run `roam runs verify` with the
  receipt-integrity extension (`verify_chain_with_receipts`) to detect
  receipt-file tampering offline. Treat the chain-verification result
  as authoritative over a bare on-disk receipt.

---

## Schema-stability commitment

`McpDecisionReceipt` does not yet expose a `schema_version` field
directly; the receipt is wrapped by the broader `ChangeEvidence` envelope
which carries `schema_version: "1.0.0"` and follows the
`_W210_OMIT_WHEN_DEFAULT_FIELDS` discipline (additive bumps remain
byte-identical for packets that don't populate the new fields). The
recommended pin for gateway integrations today: track the receipt by its
content hash and the active roam version reported via `roam --version`.

## Schema export

A portable JSON Schema (Draft 2020-12) describing the receipt shape is
emitted by `scripts/export_mcp_receipt_schema.py`, which delegates to
`roam.evidence.mcp_receipt_schema.mcp_receipt_json_schema()` (MCP-P2.2,
shipped 2026-05-18). The schema's `$id` is versioned
(`https://roam-code.com/schema/mcp-receipt/v1.json`) so gateways can
pin and detect breaking-change bumps. Closed enums (`REDACTION_REASONS`,
`_POLICY_DECISIONS`) and the SHA-256 hex pattern are pulled by
reference from the canonical vocabulary at build time, so a vocabulary
edit propagates into the schema document without a separate edit. The
`mcp-server-card` `_meta` advertisement of the schema URL remains a
follow-on.

**Stability rules under v1 `$id`.**

- The closed enums (`REDACTION_REASONS`, `_POLICY_DECISIONS`) are
  append-only. Removing a member is a breaking change; adding one is
  additive.
- The receipt dataclass fields are append-only. Existing fields keep
  their type signature; new fields land as `Optional` with sensible
  defaults so older parsers can ignore them.
- `extra` is forward-compat by construction. Keys can land without a
  version bump. Gateways that need a structural guarantee on a field
  inside `extra` should request promotion to a top-level field.

---

## Roadmap

Source of truth: `dev/BACKLOG.md` § "MCP runtime security — surfaced
2026-05-18". The items below are quoted at the granularity a gateway
integrator needs to plan around; consult BACKLOG for the implementation
detail.

### P0 — claim-integrity (shipped today)

| Item       | Status                       | Gateway impact                                                                                                                       |
| ---------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| MCP-P0.1   | **shipped** (2026-05-18)     | `redactions=("secret",)` reflects egress redaction lineage; `extra.redaction_details` carries hits per pattern.                       |
| MCP-P0.2   | **shipped** (2026-05-18)     | `policy_decision` is now a closed-enum (`allow` / `deny` / `not_evaluated`) decision from the MCP-boundary mode gate.                 |
| MCP-P0.3   | **shipped** (2026-05-18)     | Receipt sha256 anchored in the signed ledger; `verify_chain_with_receipts` adds `receipt_integrity` (`ok` / `missing` / `tampered` / `not_linked`). |

### P1 — coverage closure

| Item       | Status                          | Gateway impact                                                                                                  |
| ---------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| MCP-P1.1   | **shipped** (2026-05-18)        | Shadow-mode env flag (`ROAM_MODE_DRY_RUN`) + finding emission via `src/roam/mcp_server.py` policy gate. Gateways can run roam in observe-only without disabling enforcement; receipts carry `policy_decision=would_deny_dry_run`. |
| MCP-P1.2   | queued                          | Prompt-injection marker scan on egress. Tags `redactions` with `prompt_injection_marker` (new enum member).     |

### P2 — public surface

| Item       | Status                          | Gateway impact                                                                                              |
| ---------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| MCP-P2.1   | **this document** (shipped)     | Public integration contract.                                                                                |
| MCP-P2.2   | **shipped** (2026-05-18) — schema export | Standalone `McpDecisionReceipt` JSON Schema export landed via `scripts/export_mcp_receipt_schema.py` → `roam.evidence.mcp_receipt_schema.mcp_receipt_json_schema()` (Draft 2020-12, `$id` versioned `.../mcp-receipt/v1.json`). `mcp-server-card` `_meta` advertisement still queued as a follow-on. |

---

## References

- Source modules
  - `src/roam/evidence/mcp_receipt.py` — `McpDecisionReceipt` dataclass.
  - `src/roam/evidence/_vocabulary.py` — closed enums (`REDACTION_REASONS`, `POLICY_DECISIONS`).
  - `src/roam/security/redact.py` — secret-pattern set + `redact_secrets_in_string`.
  - `src/roam/runs/ledger.py` — run-ledger substrate.
  - `src/roam/runs/signing.py` — HMAC chain + `verify_chain` 4-state
    envelope + `verify_chain_with_receipts` (P0.3, lines 414-518) for
    the `receipt_integrity` extension; `RECEIPT_INTEGRITY_STATES`
    closed enum at lines 394-401.
  - `src/roam/modes/policy.py` — 4-mode policy substrate.
  - `src/roam/mcp_server.py` — MCP wrappers, receipt egress wiring
    (P0.1), mode-gate enforcement at the MCP boundary (P0.2:
    `_evaluate_mcp_mode_policy` + `_build_mode_blocked_envelope`),
    shadow-mode flag (P1.1: `ROAM_MODE_DRY_RUN`).
  - `scripts/export_mcp_receipt_schema.py` — P2.2 schema-export entry
    point; delegates to the canonical builder.
  - `src/roam/evidence/mcp_receipt_schema.py` — `mcp_receipt_json_schema()`
    + `SCHEMA_ID` (`https://roam-code.com/schema/mcp-receipt/v1.json`)
    + `SCHEMA_VERSION`.
  - `tests/test_w_mcp_receipt_hmac_link.py` — covers the P0.3
    receipt-integrity verdict matrix.
  - `tests/test_mcp_receipt_json_schema.py` — covers the P2.2 schema
    export (Draft 2020-12 conformance + closed-enum lock-step).
  - `tests/test_w_mcp_security_pipeline_e2e.py` — end-to-end pipeline
    coverage across P0.1–P0.3 + P1.1.
- Public surfaces
  - `templates/distribution/landing-page/docs/mcp-usage.html` § `#security-stance`.
  - Discussion #37 — public reply at
    `https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163`.
- Internal roadmap
  - `dev/BACKLOG.md` § "MCP runtime security — surfaced 2026-05-18".

---

## Wording discipline

Roam *maps to* and *supports evidence for* the controls described above.
It does NOT *certify* compliance, *make customers compliant*, or replace
a gateway's policy-enforcement role. Where this document describes
enforcement, it means structural enforcement at the server boundary —
coarse flags, closed enums, mode gates — not the semantic policy
decisions that belong to the gateway and the host.
