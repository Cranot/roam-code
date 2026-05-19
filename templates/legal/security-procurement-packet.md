# Security and procurement packet — Roam Code

> **REVIEW BEFORE USE.** v2 draft, 2026-05-18 (engine `roam-code`
> v13.2). The single bundle a vendor reviewer receives during a
> procurement review of Roam Code's services. Pre-fills the questions a
> typical security or procurement review will ask. Pair with the
> [DPA](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md)
> (signed before repo access for EU customers),
> the [PR Replay SOW](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md),
> the [mutual NDA](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md),
> and the live posture pages at
> [`roam-code.com/trust`](https://roam-code.com/trust) (compliance
> stance + framework table),
> [`roam-code.com/audit`](https://roam-code.com/audit) (PR Replay
> sample report + engagement scope),
> [`roam-code.com/security`](https://roam-code.com/security) (VDP +
> supply-chain integrity), and
> [`roam-code.com/privacy`](https://roam-code.com/privacy).
> Bracketed placeholders `[LIKE_THIS]` are filled in per engagement.
> Forward-looking content covering Roam Review (planned hosted PR bot)
> is marked explicitly; the Stripe + GitHub sub-processor scope listed
> in **Section 4** is the only scope live at v1.

This packet covers:

- **Roam Code CLI** (live; Apache 2.0; 100% local — `pip install
  roam-code`; 241 commands / 227 MCP tools / 28 finding detectors).
- **PR Replay** (live; one-shot structural-review engagement priced
  per [SOW §1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#1-engagement)
  — Team / Deep tiers).
- **Roam Review** (planned hosted GitHub App; sections marked "planned"
  describe the design target, not a current product).

Roam Cloud (planned metrics dashboard) is out of scope of this packet.

---

## 0. Parties (procurement intake)

This packet is provided by:

- **Provider**: `[PROVIDER_LEGAL_NAME]`, `[TBD: PROVIDER_LEGAL_FORM]`
  (e.g., Greek sole-trader / atomiki epicheirisi), tax ID
  `[TBD: PROVIDER_TAX_ID]` (Greek `[TBD: PROVIDER_AFM]` / EU VAT-ID /
  other), of `[PROVIDER_ADDRESS]`. Symmetric with
  [NDA §1](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md)
  party identification and
  [SOW "Parties and effective date"](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#parties-and-effective-date).

A mutual NDA (template:
[`nda-mutual.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md))
may be signed before this packet is shared in unredacted form, at
Provider's or Buyer's request. Where a downstream SOW or DPA is
subsequently signed,
[NDA §10A](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md#10a-relationship-to-sow-and-dpa)
governs the relationship between the three documents.

---

## 1. One-paragraph summary for legal

**Roam Code CLI (live).** Roam Code's flagship product is the
open-source `roam-code` CLI: a **100% local Apache 2.0 tool** that
requires no API key, no vendor cloud endpoint, and no inbound network
egress to Provider or any third party for analysis. Customer source
code and Personal Data stay on Customer's machine; Provider has no
analysis-time data flow. This local-only stance is contractually
committed in
[DPA §6 (Security measures)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#6-security-measures).

**PR Replay engagements (live).** For one-shot structural-review
engagements priced per
[SOW §1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#1-engagement),
Provider clones the agreed repository to a temporary working tree on
Provider's local machine, runs `roam-code` locally, delivers a
markdown + PDF report, and deletes the clone within 7 calendar days of
report delivery per
[DPA §7](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention).
No customer source code traverses a Provider-operated cloud service;
the only PR Replay sub-processors are Stripe (billing) and GitHub
(only where Customer authorises repository access). See **Section 4**.

**Roam Review (planned hosted GitHub App).** When the hosted product
launches, on every pull request Roam Review will clone the head branch
into an ephemeral sandbox, run the open-source `roam-code` CLI to
produce a structural-risk verdict, post a single sticky comment on the
PR, and discard the working copy. Source code is read in transit,
processed in-memory, and not persisted. The only artefacts retained
are: the verdict envelope (a JSON metadata blob, no source bodies),
an append-only audit-trail record, and operational logs scrubbed of
source content. Hosted Roam Review sub-processor scope, retention
windows, and security schedule will be completed in
[DPA §5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors)
and DPA §6 before launch, per
[DPA §2 (Processing description)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#2-processing-description).

**No model training.** Customer source code, diffs, comments,
identifier names, or metrics are never used to train, fine-tune,
evaluate, or benchmark any machine-learning model owned by Provider
or shared with third parties, across all three product surfaces. This
matches
[DPA §6 ("No training")](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#6-security-measures)
and [SOW §9 ("No training")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling).

Provider is the data controller for billing and account data (data
subjects: Customer's billing contacts and admin users) and the data
processor for PR content during analysis (data subjects: Customer's
developers and any individuals named in source comments or commits).

---

## 2. Data flow

### 2.1 Roam Code CLI (live)

```
[1] Developer runs `roam <command>` on a local checkout
        │
        ▼
[2] CLI parses the working tree and writes a local SQLite database
    under .roam/ (in-repo). No network calls. No telemetry.
    No phone-home. No inbound network listener.
        │
        ▼
[3] CLI writes output to stdout (text / JSON / SARIF) on the
    developer's local machine. Provider has no analysis-time visibility.
```

Per
[DPA §6](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#6-security-measures):
**100% local, no API key, no vendor cloud endpoint** — no
analysis-time data flow to Provider for the CLI surface. Same stance is
documented publicly at
[`roam-code.com/security#supply-chain`](https://roam-code.com/security#supply-chain) and
[`roam-code.com/trust#artifacts`](https://roam-code.com/trust#artifacts)
(data-flow diagram row).

### 2.2 PR Replay engagement (live)

```
[1] Buyer pays via Stripe Payment Link or agrees manual invoice path
        │
        ▼
[2] Buyer countersigns SOW; grants Provider read-only deploy-key or
    equivalent least-privilege access (DPA §4.3)
        │
        ▼
[3] Provider clones the repo to a temporary working tree on Provider's
    local working machine (encrypted storage; consumer cloud-sync
    folders excluded — DPA §6)
        │
        ▼
[4] Provider runs `roam pr-replay --range <commits>` locally;
    deterministic structural analysis; no AI provider in the loop by
    default (DPA §8)
        │
        ▼
[5] Provider produces markdown + PDF report; delivers via direct email
    from Provider's mailbox (DPA §5 row 4)
        │
        ▼
[6] Walk-through call held at the agreed slot
        │
        ▼
[7] Provider deletes the temporary clone within 7 calendar days of
    report delivery (DPA §7.1)
        │
        ▼
[8] Engagement ledger entry retained for up to 2 years (DPA §7.4);
    invoice + accounting records retained for the period required by
    Greek tax law (DPA §7.5)
```

### 2.3 Roam Review (planned hosted GitHub App)

```
[1] Pull request opened or pushed
        │
        ▼
[2] GitHub sends webhook to Roam Review
    (HTTPS, HMAC-SHA-256-signed, signature verified before processing)
        │
        ▼
[3] Roam Review fetches the PR head + base via the GitHub App installation token
    (read-only access, scope listed in §3 below)
        │
        ▼
[4] Ephemeral sandbox container starts (empty, network-restricted)
        │
        ▼
[5] Repository contents cloned into the sandbox at the PR head SHA
        │
        ▼
[6] `roam pr-analyze --json --rules .roam/rules.yml --audit-trail` runs in-process,
    in-memory; produces a verdict envelope (JSON, no source bodies)
        │
        ▼
[7] `roam pr-comment-render` formats a single sticky markdown comment
        │
        ▼
[8] Comment posted to PR via GitHub App installation token
        │
        ▼
[9] Sandbox container destroyed; working copy of the source is gone
        │
        ▼
[10] Verdict envelope + audit-trail record written to encrypted-at-rest store
     (no source content, only metadata: file paths, symbol names, scores)
```

**Failure-path handling:**

- **[6a]** On sandbox crash, the container is terminated immediately; no crash dumps or core files are written to persistent storage.
- **[6b]** Stack traces forwarded to the error-tracking sub-processor (Sentry) have file paths truncated to repo-relative and are stripped of source content; no function bodies, comments, or string literals appear in error reports.
- **[6c]** Sandbox-orchestrator metadata (job-id, repo-id, SHA, timing, exit code) is logged and retained per §5 ("Operational logs"); this metadata contains no source content.
- **[6d]** No backup snapshots are taken of the sandbox volume itself. The sandbox is treated as fully ephemeral; persistence happens only at step [10].
- **[6e]** If the analysis run exceeds the wallclock budget (default 5 minutes; configurable per tier), the sandbox is terminated and the PR comment surfaces "analysis timeout — re-run via /roam re-review" instead of a verdict.

Time from webhook to comment: target p95 under 90 seconds for repositories under 100k LOC; tier-specific commitments in §9.

---

## 3. GitHub App permissions (planned Roam Review)

The planned Roam Review GitHub App will request the **minimum scope**
required for the §2.3 data flow above. The CLI surface (§2.1) and PR
Replay engagements (§2.2) do not require a GitHub App; PR Replay uses a
read-only deploy-key or equivalent least-privilege access per
[DPA §4.3](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#4-controller-obligations).

| Permission | Scope | Required for |
|---|---|---|
| `Contents` | read | Cloning the PR head and base for analysis |
| `Pull requests` | read & write | Reading the PR diff; posting + updating the sticky verdict comment |
| `Metadata` | read (mandatory) | GitHub requires this for any installed app |
| `Checks` | read & write | Optional; only enabled if you opt in to BLOCK-as-a-failed-check on `BLOCK` verdicts |
| `Statuses` | read & write | Optional; alternative to Checks for older repos |

The app does **not** request: `Administration`, `Repository projects`, `Members`, `Issues` (write), `Workflows`, `Secrets`, `Environments`, `Actions`, or any organisation-level write permission.

If your organisation enforces fine-grained permission review, the manifest is published and reviewable at `github.com/apps/roam-review` before installation.

---

## 4. Sub-processors

Authoritative source:
[DPA §5 (Sub-processors)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors).
The table below mirrors DPA §5 verbatim for v1 (PR Replay + CLI),
with hosted Roam Review rows flagged as planned.

| Sub-processor | Purpose | Personal Data scope | Location / transfer basis |
|---|---|---|---|
| Stripe, Inc. | Payment processing, receipts, refunds, and billing records. | Billing contact and transaction metadata only; no source code. | USA; Stripe transfer terms / SCCs where applicable. |
| GitHub, Inc. | Repository access when Controller chooses GitHub collaborator, deploy-key, GitHub App, or webhook access. | Repository content, git metadata, PR metadata, and user metadata needed for the agreed service. | USA / global; GitHub transfer terms / SCCs where applicable. |
| `[TBD: HOSTING_PROVIDER_IF_ANY]` (planned Roam Review) | Hosted Roam Review or Cloud infrastructure, if enabled. **Not applicable to PR Replay (local-only) or the CLI (local-only).** | `[TBD: DATA_SCOPE]` | `[TBD: REGION_AND_TRANSFER_BASIS]` |
| `[TBD: EMAIL_OR_STORAGE_PROVIDER_IF_ANY]` | Delivery of reports, support, or encrypted artifact storage, if used. For PR Replay v1 delivery is via direct email from Provider's mailbox. | Contact details and report artifacts. | `[TBD: REGION_AND_TRANSFER_BASIS]` |

The CLI itself has **no runtime sub-processors** — it executes
entirely on the developer's machine (§2.1).

**Change-notice SLA.** Provider will not add a new sub-processor
without at least **14 calendar days'** prior notice, unless emergency
replacement is necessary to maintain security or service continuity,
per
[DPA §5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors).
Controllers may object on reasonable data-protection grounds by
emailing `security@roam-code.com`. **If an objection cannot be
resolved, Customer may terminate the affected service for convenience
and receive any refund required by the Agreement; data is exported
(per §10) and deleted (per §5 and
[DPA §7](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention))
before the termination effective date.** Same change-notice cadence
is restated on the public
[`roam-code.com/privacy#sub-processors`](https://roam-code.com/privacy#sub-processors)
and [`roam-code.com/trust#artifacts`](https://roam-code.com/trust#artifacts)
pages.

Concrete hosted-Roam-Review vendor names will be disclosed (under
NDA where requested) pre-contract and published at GA. Bracketed
placeholders above are replaced in evaluation packets shared after a
mutual NDA is in place (template:
[`nda-mutual.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md)).

---

## 5. Data retention

Authoritative source for PR Replay retention:
[DPA §7 (Return, deletion, and retention)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention).
The CLI surface stores no data on Provider systems (§2.1), so retention
does not apply to the CLI.

### 5.1 PR Replay engagements (live)

| Artefact | Default retention | Authority |
|---|---|---|
| Temporary repo clone, git bundles, indexes, derivative working files | Deleted within **7 calendar days** of report delivery or service termination | [DPA §7.1](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention) |
| Final delivered report (markdown + PDF) | Up to **90 days** to support follow-up questions; earlier deletion on written request | [DPA §7.3](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention) |
| Engagement ledger entries (`tier`, `client`, `commits scanned`, `output path`, `generated_at`) | Up to **2 years** for audit defence, reconciliation, dispute handling | [DPA §7.4](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention), [SOW §9](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling) |
| Invoices, accounting records, and legally required tax records | Period required by applicable law (Provider is Greek-established) | [DPA §7.5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention) |
| Anonymised aggregate detector improvements (the "dogfood right") | Indefinite, only where no Client, repo, contributor, code, identifier, or quote can be reconstructed | [DPA §7.6](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention), [SOW §10 ("Aggregate detector improvements")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#10-intellectual-property) |

Written deletion confirmation is provided on request per
[DPA §7.2](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention).
Client may request deletion of all engagement artefacts at any time
after acceptance, subject to legally required retention of the invoice
and ledger entry, per
[SOW §9 ("Right to delete")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling).

### 5.2 Roam Review (planned hosted GitHub App)

Hosted service retention windows will be filled in the relevant order
form before launch, per
[DPA §7 ("Hosted service retention must be filled in the relevant order form before launch")](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention).
The design targets below are **not yet contractually binding** and are
subject to change before GA. Once GA is announced, the binding
retention table moves into the order form.

| Artefact (planned) | Design-target retention | Authority |
|---|---|---|
| Working copy of cloned source | Destroyed on sandbox shutdown (target p95 < 5 min) | Design |
| Verdict envelope (no source) | Tier-dependent (planned: 90 days Starter, 1 year Team, 3 years Business, customer-defined Scale) | Design |
| Audit-trail records (no source) | Same as verdict envelope | Design |
| Operational logs (path + sha + verdict; no source content) | 30 days (planned) | Design |
| Billing data | Period required by applicable law (Provider is Greek-established) | [DPA §7.5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention) |
| Account metadata (org name, owner email) | Lifetime of subscription + 30 days post-cancellation (planned) | Design |

---

## 6. No training, no retention of source content

Provider commits, contractually:

1. **No model training.** Customer source code, diffs, comments,
   identifier names, or metrics are never used to train, fine-tune,
   evaluate, or benchmark any machine-learning model owned by Provider
   or shared with third parties. This commitment is restated in
   [DPA §6 ("No training")](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#6-security-measures)
   and [SOW §9 ("No training")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling).
   Optional third-party AI providers are off by default and require a
   separate written opt-in per
   [DPA §8 (Optional AI providers)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#8-optional-ai-providers);
   the named provider is added to
   [DPA §5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors)
   before processing begins.
2. **No source persistence beyond analysis.** For the CLI, no source
   leaves the developer's machine (§2.1). For PR Replay, the
   temporary clone is deleted within 7 calendar days of report
   delivery per
   [DPA §7.1](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention).
   For planned Roam Review, the cloned working copy exists only for
   the duration of one analysis run (design target p95 < 5 minutes)
   and is destroyed when the sandbox terminates; no backup snapshots
   are taken of the sandbox volume.
3. **No source in logs.** Operational logs include file paths
   (truncated to repo-relative), symbol names, line numbers, verdict
   scores, and run-IDs, but not function bodies, comments, or string
   literals.
4. **No source in metrics.** The metrics surfaced to your team (or
   via the API) are aggregated counts and scores. Source content is
   not part of any payload, dashboard, or notification.
5. **Encryption.** TLS 1.2+ in transit; AES-256 at rest for retained
   artefacts. Sub-processor encryption configurations are listed at
   [`roam-code.com/security`](https://roam-code.com/security).

If any of those statements stops being true, customers receive at
least **14 calendar days'** notice (matching the sub-processor
change-notice cadence in §4 and
[DPA §5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors))
and an opt-out path before the change takes effect. Material breaches
trigger the incident-notification flow in §8.

---

## 7. Compliance posture

Provider makes the following statements; please verify against your
own framework requirements. Where a framework is listed, Provider's
artefacts **map to** and **support evidence for** the controls —
they do not **certify** Customer's codebase as compliant or **make**
Customer compliant. This mirrors the honest-banner language in
[SOW §12.1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#121-warranty-disclaimer)
and
[NDA §12](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md#12-no-certification-or-compliance-claim),
and the public posture pages at
[`roam-code.com/trust#disclaimer`](https://roam-code.com/trust#disclaimer)
and
[`roam-code.com/governance`](https://roam-code.com/governance).

| Framework | Status | Evidence |
|---|---|---|
| GDPR | Applicable; Provider is a Processor for PR-content during analysis. EU-based controllers receive a signed DPA before access ([`templates/legal/dpa.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md)). | DPA + [Privacy Policy](https://roam-code.com/privacy) |
| SOC 2 CC8.1 (change management) | **Not currently certified.** Honest target: **SOC 2 Type II report by Q1 2027.** Until then, the audit-trail subsystem produces evidence artefacts (signed in-toto v1 attestations, exportable JSONL, OSCAL projection via `roam evidence-oscal`) that **map to** and **support evidence for** a SOC 2 CC8.1 evidence package. Provider makes no representation that the artefacts alone satisfy CC8.1 — your auditor judges that. | `roam audit-trail-export` + `roam evidence-oscal` output |
| ISO/IEC 42001 (AI management system) | **Not currently certified.** Honest target: **ISO/IEC 42001 certification by Q3 2027.** Until then, Provider's structural-review records and signed attestations **map to** documentation/monitoring inputs that customers may use in their own ISO 42001 evidence package. Standalone Provider certification is on the roadmap, not at launch. | `roam audit-trail-conformance-check` + `roam evidence-oscal` output |
| ISO/IEC 27001 | **Not currently certified.** Roadmapped after SOC 2 Type II. | — |
| EU AI Act Article 12 | Article 12 logging applies only to providers of high-risk AI systems listed in Annex III. Code-generation tooling is not in Annex III, so Provider's services are out of scope of Article 12. For customers whose *own product* is in Annex III, Provider's signed review attestations are usable as Article 14 human-oversight evidence — not Article 12 logs themselves. | `roam article-12-check` (scoping/readiness assessment, not certification) |
| NIST AI RMF | Not certified; Provider's evidence artefacts may **map to** RMF Govern/Map/Measure/Manage functions. | [`roam-code.com/governance`](https://roam-code.com/governance) |

Provider does not make compliance claims it cannot evidence. If a
framework is missing from the table and you need it, email
`hello@roam-code.com`.

---

## 8. Incident response

- **Contact**: `security@roam-code.com` (PGP key auto-published by
  Proton; discoverable via the
  [security.txt](https://roam-code.com/.well-known/security.txt) file
  at the standard well-known location).
- **Acknowledgement target**: **one business day** (Athens, Greece
  time), with a substantive triage response within **two business
  days**. For high-severity issues Provider engages immediately on
  receipt. These SLAs match the public commitments on
  [`roam-code.com/security#reporting-a-vulnerability`](https://roam-code.com/security)
  and
  [`roam-code.com/trust#artifacts`](https://roam-code.com/trust#artifacts).
- **Notification**: customers affected by a confirmed Personal Data
  breach are notified without undue delay and, where feasible, within
  **72 hours** of becoming aware, per
  [DPA §9 (Personal Data breach notification)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#9-personal-data-breach-notification)
  (GDPR Article 33 alignment).
- **Disclosure window**: default public-disclosure window is 90 days
  from initial report; Provider may extend on reporter request. See
  the full coordinated-disclosure policy at
  [`roam-code.com/security`](https://roam-code.com/security).
- **Status page**: [`roam-code.com/status`](https://roam-code.com/status)
  shows live availability and incident history (planned).
- **Right to audit**: per
  [DPA §10 (Audits)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#10-audits),
  Customer may audit Provider's compliance once per year on at least
  30 days' written notice. Planned Roam Review Business and Scale
  tier customers may additionally request:
  - A remote SOC 2-style controls walkthrough (~2 hours)
  - The executive summary of the latest third-party penetration test (no raw findings)
  - The SBOM + cosign attestation for the deployed image
  Scale-tier customers may additionally request, under mutual NDA
  ([`nda-mutual.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md)),
  in-person inspection of the operational environment and the raw
  penetration-test report. Starter and Team tier customers may
  request a written response to the SIG-Lite or CAIQ questionnaire
  (delivered within 10 business days) in lieu of a controls
  walkthrough.

---

## 9. SLA snapshot

### 9.1 PR Replay engagement SLA (live)

PR Replay timelines are governed by
[SOW §4 (Timeline)](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#4-timeline):

| Tier | Delivery window | Walk-through |
|---|---|---|
| Team (USD $2,500 — [SOW §1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#1-engagement)) | 5 business days from kickoff | 30-minute call |
| Deep (USD $6,000 — [SOW §1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#1-engagement)) | 10 business days from kickoff | 90-minute call + written 90-day remediation plan |

Business days are calculated against Provider's calendar (Greek
public holidays observed). Refunds and cancellation are governed by
[SOW §8](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#8-refunds-and-cancellation)
and the public [refund policy](https://roam-code.com/refund),
including the EU 14-day right of withdrawal where applicable.

### 9.2 Roam Review SLA (planned — design targets, not commitments)

The table below shows **GA design targets** for the planned hosted
Roam Review GitHub App. Pre-GA (during the design-partner programme)
all SLAs are **best-effort**, with no service credits — pilot
customers are remediated with a free month for any tier-grade
incident. SLAs become contractually binding 90 days after the
published GA date, once a trailing-12-month uptime sample is published
at [`roam-code.com/status`](https://roam-code.com/status).

| Tier (planned) | Uptime target (GA) | p95 webhook-to-comment (GA target) | Support response | Credits (GA) |
|---|---|---|---|---|
| Starter | 99.0% | 90 seconds | 2 business days, email only | None |
| Team | 99.5% | 60 seconds | 1 business day, email only | 5% / 0.5pp under target |
| Business | 99.9% | 30 seconds | 4 business hours, email + Slack-shared channel | 10% / 0.5pp under target |
| Scale | 99.95% | 30 seconds | 1 business hour, named technical contact | 20% / 0.5pp under target |

Trailing-12-month uptime data is published at
[`roam-code.com/status`](https://roam-code.com/status) from GA + 90
days; SLAs become contractually binding on tier upgrade after that
data is public. Until then, the values above are design targets, not
commitments.

---

## 10. Quick-reference Q&A

> **Where is data hosted?**
> For the **CLI** (live): on the developer's machine; Provider has no
> hosted infrastructure for the CLI surface. For **PR Replay** (live):
> on Provider's local working machine in Greece (EU); see
> [DPA §11 (Cross-border transfers)](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#11-cross-border-transfers).
> For **Roam Review** (planned): EU by default; US opt-in to be
> announced at GA. Hosting provider is `[TBD: HOSTING_PROVIDER]` per
> [DPA §5](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#5-sub-processors).

> **Do you have SOC 2?**
> Not yet. Honest target: **SOC 2 Type II report by Q1 2027** (ISO/IEC
> 42001 by Q3 2027). The audit-trail subsystem is built to **map to** a
> SOC 2 CC8.1 evidence package and to project into OSCAL v1.2 via `roam
> evidence-oscal` ahead of the audit. See §7 above, the technical
> substrate in §11.1–§11.4, and the public
> [`roam-code.com/trust`](https://roam-code.com/trust) framework table.

> **Can you sign our standard infosec questionnaire (CAIQ / SIG / VSAQ)?**
> Yes for planned Roam Review Business and Scale customers; the
> response template is at
> [`templates/legal/sig-lite-response.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/sig-lite-response.md)
> (v1 draft). PR Replay buyers can request a written response at
> `hello@roam-code.com`.

> **Can you run on-prem or in our VPC?**
> Yes for the **CLI** today — it runs entirely on your machine
> (Apache 2.0;
> [`pip install roam-code`](https://pypi.org/project/roam-code/)).
> Not as a packaged GA hosted product today. A regulated
> private-deployment pilot of Roam Review can be scoped in a signed
> SOW, with deployment artefacts, support terms, data flow, and
> responsibilities written into the order form before work starts.

> **What happens if you go away?**
> The OSS CLI
> ([`pip install roam-code`](https://pypi.org/project/roam-code/),
> [Apache 2.0](https://github.com/Cranot/roam-code/blob/main/LICENSE))
> gives you the same engine on your machine, indefinitely. PR Replay
> reports are delivered to a Client-nominated email and may be
> redistributed internally without restriction per
> [SOW §10 ("Report ownership")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#10-intellectual-property).
> For planned Roam Review, your audit trail will be exportable as
> JSONL on demand. There is no proprietary lock-in at the engine
> layer.

> **Right to delete?**
> For **PR Replay**: per
> [SOW §9 ("Right to delete")](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling)
> and
> [DPA §7](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md#7-return-deletion-and-retention),
> Client may request deletion of all engagement artefacts at any time
> after acceptance; Provider complies within 30 days, except where
> Greek tax law requires retention of the invoice and ledger entry
> (typically 5 years per
> [SOW §9](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#9-confidentiality-and-data-handling)).
> For planned **Roam Review** (design target): subscription metadata
> deleted within 30 days of written cancellation request; verdict
> envelopes + audit-trail records retained for the lesser of the
> remaining tier-retention window or 90 days post-cancellation, then
> permanently deleted; customers are offered an export at least 30
> days before any post-cancellation deletion. Operational logs roll
> over per the retention table in §5.

---

## 11. Attestation and evidence substrate

Where this packet is bundled with `roam audit-evidence-pack` output
for a specific repository, the bundle includes a Cosign / Sigstore
signature so the receiving security team can verify the JSON
metadata, the audit-trail file, and the conformance score have not
been tampered with in transit. Verification command and key are
documented at
[`roam-code.com/security#supply-chain`](https://roam-code.com/security#supply-chain).
The evidence artefacts produced by `roam-code` **map to** and
**support evidence for** the framework controls referenced in §7;
they do not constitute formal conformity assessment (matching
[NDA §12](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md#12-no-certification-or-compliance-claim)
and
[SOW §12.1](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md#121-warranty-disclaimer)).

### 11.1 Local evidence substrate (CLI; live in v13.2)

The CLI ships a portable evidence substrate stored under `.roam/` in
the repo. All four streams are local-filesystem, zero-network, and
schema-stable for v13.x. None require Provider infrastructure to
operate:

| Substrate | Storage | Purpose | Verification |
|---|---|---|---|
| **HMAC-chained run ledger** | `.roam/runs/` (per-run event JSONL + signed `meta.json`) | Tamper-evident record of every gated agent action (preflight, impact, critique, diff). | `roam runs verify` (offline; detects chain breaks without contacting Provider). |
| **4-mode policy substrate** | `.roam/modes/` + `.roam/constitution.yml` | Declares the action surface per run: `read_only` / `safe_edit` / `migration` / `autonomous_pr`. Each mode is a cumulative authority envelope. | `roam mode` reads + prints the active mode; the run ledger records the mode in force at each event. |
| **Findings registry** | `.roam/findings.sqlite` | Normalised cross-detector finding table (28 detectors are wired for findings-registry persistence as of v13.2 — clones, dead, complexity, smells, n1, missing-index, over-fetch, bus-factor, auth-gaps, vulns, invariants, hotspots, taint, vibe-check, orphan-imports, conventions, pr-risk, duplicates, audit-trail-conformance/-verify, boundary, test-hermeticity, plus aggregators; the registry stores last-run state per detector, not a cumulative tally). | `roam findings list / show / count` for per-detector totals; `<DETECTOR>_DETECTOR_VERSION` stamps for drift tracking. |
| **`ChangeEvidence` packet** | Emitted by `roam pr-bundle emit` (signed, hash-stable) | One portable evidence packet per change scope. Carries actor identity, authority refs, environment, policy decisions, blast radius, findings, redactions, content hash, schema version. | Hash-stable golden parity tests pin canonical JSON byte-output across releases. Validates with `roam pr-bundle validate --strict --strict-resolved` (CI flag `--ci` implies both). |

### 11.2 Signed PR bundles, CGA, and VSA

`roam pr-bundle emit` produces a proof-carrying PR bundle (preflight +
impact + critique + diff + verdict envelope). When combined with the
CGA (`roam cga`) and VSA sibling outputs, the bundle is a self-contained
proof of what an agent saw, what it was authorised to do, and what was
verified. Verification is offline and signature-rooted — receiving
teams need only the public Cosign / Sigstore key and the bundle file.
See `roam attest` for the in-toto v1 attestation surface.

### 11.3 MCP per-tool-call decision receipts

For sensitive MCP tool calls (the `safe_edit` / `migration` /
`autonomous_pr` modes from §11.1), roam emits an `McpDecisionReceipt`
(authoritative source: `src/roam/evidence/mcp_receipt.py`). Each
receipt is a deterministically-serialised, content-hashed JSON
artefact recording: MCP client id, tool id, declared side-effects,
inputs hash, decision (allow / deny / require-approval), and outputs
reference. Receipts are designed to be tailed by a gateway / PEP
(Interlock, Lasso, Portkey, MintMCP, MCP Manager, Operant) without
that gateway needing to call Provider. The full server-vs-gateway
control split is documented at
[`dev/MCP-SECURITY-POSTURE.md`](https://github.com/Cranot/roam-code/blob/main/dev/MCP-SECURITY-POSTURE.md)
and in the
[`mcp-usage.html#security-stance` section](https://roam-code.com/docs/mcp-usage.html#security-stance)
of the docs site.

**MCP server vs gateway boundary (procurement-relevant summary).** roam
owns: read-only / destructive flags per tool, scope-based tool
filtering via the 4-mode policy substrate, integrity of tool
descriptions returned to the host, and structured-evidence emission.
roam does NOT own: cross-server policy, audit aggregation across
multiple MCP servers, shadow-mode rollout, semantic response-content
scanning, or tenant isolation — those are gateway concerns by design,
and the receipt + ledger streams are produced precisely so a gateway
can consume them.

### 11.4 OSCAL v1.2 control mapping export

`roam evidence-oscal` projects the `ChangeEvidence` packet plus the
`templates/audit-report/control-mapping.yaml` registry into an
OSCAL-shaped JSON document (component definition + assessment plan +
assessment results fragments). This is the canonical format for a
customer's GRC tooling to ingest Provider evidence. As with §7, the
export **maps to** controls and **supports evidence for** them; it
does not certify the customer's codebase as compliant with any
framework — that judgement stays with the customer's auditor.

A wording-guard CI lint (`tests/test_doc_consistency.py` +
`tests/_helpers/wording_lint.py`) blocks the strings `certif*`,
`compliant`, and (in compliance context) `guarantee` from appearing in
generated reports outside an explicit negation window. Every shipped
artefact passes this lint.

---

## 12. Operational security

| Control | Status |
|---|---|
| **Penetration testing** | Annual third-party penetration test scheduled from Roam Review GA. Executive summary available to Business+ customers under NDA; Scale-tier customers may request the raw report under mutual NDA ([`nda-mutual.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md)). |
| **Vulnerability disclosure policy** | Public VDP at [`roam-code.com/security`](https://roam-code.com/security); PGP key at [`/.well-known/security.txt`](https://roam-code.com/.well-known/security.txt). Acknowledgement within **one business day**; substantive triage within **two business days**; default 90-day public-disclosure window (extendable on reporter request). Safe-harbour commitment for good-faith research. |
| **Bug bounty** | `[TBD: bug-bounty policy and reward tiers — pending live-payment setup]`. Until then, public acknowledgement in the [hall of thanks](https://roam-code.com/security#hall-of-thanks). Same placeholder is cited from the public [trust](https://roam-code.com/trust#artifacts) and [security](https://roam-code.com/security#hall-of-thanks) pages. |
| **SBOM (software bill of materials)** | Published per release in CycloneDX format, cosign-signed. Linked from the PyPI release page. CI pipeline emits in-toto v1 attestations for the build. PyPI releases use OIDC Trusted Publishing (no long-lived API tokens) — see [`roam-code.com/security#supply-chain`](https://roam-code.com/security#supply-chain). |
| **Dependency hygiene** | Dependabot enabled on the public repo; daily security-advisory scan; high-severity advisories patched within 7 days of public disclosure. |
| **MFA enforcement** | All employee access to production systems (cloud provider, source-control, observability, customer data) requires hardware-key (WebAuthn / FIDO2) MFA. Password-only access is disabled. |
| **Background checks** | All engineering staff with production-data access undergo a third-party background check (`[TBD: BACKGROUND_CHECK_VENDOR]`) before access is granted. Annual re-attestation. |
| **Backups + RPO / RTO** | For PR Replay: no production database; engagement ledger is a local append-only file backed up to encrypted offline storage. For planned Roam Review: Postgres daily backups, AES-256 encrypted at rest, retained 30 days; object storage versioned + immutable for 7 days; RPO 24h / RTO 4h on Business; RPO 1h / RTO 1h on Scale. |
| **Code review of Roam itself** | The `roam-code` CLI is dogfooded against itself in CI (`.github/workflows/dogfood.yml`); structural-risk verdicts on Roam's own PRs are public artefacts at [`github.com/Cranot/roam-code/pulls`](https://github.com/Cranot/roam-code/pulls). |
| **Incident drills** | Quarterly tabletop exercises against the failure modes in §2; outcome summaries available to planned Business+ Roam Review customers on request. |

`[TBD: BACKGROUND_CHECK_VENDOR]` placeholder above is replaced with
the concrete background-check provider in evaluation packets shared
after a mutual NDA
([`nda-mutual.md`](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md))
is in place.

---

## Cross-references

### Legal cluster (`templates/legal/`)

- [DPA — Data Processing Agreement](https://github.com/Cranot/roam-code/blob/main/templates/legal/dpa.md) (v2 draft; attorney review and hosted-service placeholders still required before binding execution)
- [SOW — PR Replay Statement of Work](https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md) (v2 draft)
- [NDA — Mutual Non-Disclosure Agreement](https://github.com/Cranot/roam-code/blob/main/templates/legal/nda-mutual.md) (v2 draft)
- [SIG-Lite response template](https://github.com/Cranot/roam-code/blob/main/templates/legal/sig-lite-response.md) (v1 draft)

### Public posture pages (live)

- [Trust posture](https://roam-code.com/trust) — framework table, sub-processor list, security contact, vulnerability disclosure, data-flow diagram
- [Security policy](https://roam-code.com/security) — VDP, supply-chain integrity, PGP key, hall of thanks
- [Privacy policy](https://roam-code.com/privacy) — GDPR lawful basis, retention, sub-processors (§6)
- [Audit / PR Replay](https://roam-code.com/audit) — engagement scope, tiers, [sample PR Replay report](https://github.com/Cranot/roam-code/blob/main/templates/audit-report/sample-pr-replay-team.md)
- [Governance evidence pack](https://roam-code.com/governance) — control-mapping table to SOC 2 CC8.1, ISO/IEC 42001, NIST AI RMF, EU AI Act Article 12
- [Refund policy](https://roam-code.com/refund)
- [Terms of service](https://roam-code.com/terms)
- [Status page](https://roam-code.com/status) (planned)
- [Live pricing](https://roam-code.com/pricing)

### Technical evidence substrate

- [MCP security posture (`dev/MCP-SECURITY-POSTURE.md`)](https://github.com/Cranot/roam-code/blob/main/dev/MCP-SECURITY-POSTURE.md) — server vs gateway split, the five controls, `McpDecisionReceipt` schema, integration shape for Interlock / Lasso / Portkey / MintMCP / MCP Manager / Operant
- [`mcp-usage.html#security-stance`](https://roam-code.com/docs/mcp-usage.html#security-stance) — public companion to the MCP-security-posture document

### Open-source engine

- [GitHub source — `Cranot/roam-code`](https://github.com/Cranot/roam-code) (Apache 2.0)
- [Apache 2.0 LICENSE](https://github.com/Cranot/roam-code/blob/main/LICENSE)
- [`roam-code` on PyPI](https://pypi.org/project/roam-code/)
