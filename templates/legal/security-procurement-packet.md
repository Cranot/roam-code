# Security and procurement packet — Roam Review (hosted)

> Status: **draft**, 2026-05-07. For prospects evaluating Roam Review for paid use. Pre-fills the questions a typical security or procurement review will ask. Pair with `templates/legal/dpa.md` (signed before repo access for EU customers) and the live status page at `roam-code.com/status`.

This packet covers Roam Review (hosted PR bot). Roam Cloud (metrics dashboard) and Roam Self-Hosted (in-customer-network deploy) have separate scope; ask if you need those.

---

## 1. One-paragraph summary for legal

Roam Review is a GitHub App that, on every pull request, clones the head branch into an ephemeral sandbox, runs the open-source `roam-code` CLI to produce a structural-risk verdict, posts a single sticky comment on the PR, and discards the working copy. Source code is read in transit, processed in-memory, and not persisted. The only artefacts retained are: the verdict envelope (a JSON metadata blob, no source bodies), an append-only audit-trail record, and operational logs scrubbed of source content. Customer source is never used to train any machine-learning model owned by Roam Code or shared with third parties. Roam Code is the data controller for billing and account data (data subjects: Customer's billing contacts and admin users) and the data processor for PR content during analysis (data subjects: Customer's developers and any individuals named in source comments or commits).

---

## 2. Data flow

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

## 3. GitHub App permissions

The Roam Review GitHub App requests the **minimum scope** required for the data flow above.

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

| Sub-processor | Purpose | Region | Data shared |
|---|---|---|---|
| `[CLOUD_PROVIDER]` | Compute + ephemeral sandbox + ingress | EU (default) or US (opt-in) | Source code in transit + in-memory; no source persisted |
| `[POSTGRES_PROVIDER]` | Verdict envelope + audit-trail + billing | Same region as compute | No source code; metadata only |
| `[OBJECT_STORAGE]` | Encrypted full-report archives (Team+ only) | Same region as compute | Verdict envelope JSON; no source bodies |
| Stripe, Inc. | Billing | US (EU-US Data Privacy Framework + Standard Contractual Clauses per Stripe DPA) | Billing contact, card token, subscription metadata; no source code |
| Sentry / observability provider | Error tracking | EU | Stack traces with paths truncated to repo-relative and stripped of source content; no source bodies |
| GitHub, Inc. | Source-control surface (you already use this) | US | The PR data they already host on your behalf |

Concrete vendor names are disclosed under NDA pre-contract and published at GA. Bracketed placeholders above are replaced in evaluation packets shared after a mutual NDA is in place.

Customers receive 30 days notice before any new sub-processor is added; objections may be raised by emailing `security@roam-code.com`. **If an objection cannot be resolved within 30 days of notice, Customer may terminate the affected service for convenience and receive a pro-rata refund of pre-paid fees for the unused period; data is exported (per §10) and deleted (per §5) before the termination effective date.**

---

## 5. Data retention

| Artefact | Default retention | How to change |
|---|---|---|
| Working copy of cloned source | Destroyed on sandbox shutdown (target p95 < 5 min) | Not configurable; this is the design |
| Verdict envelope (no source) | 90 days on Starter, 1 year on Team, 3 years on Business, customer-defined on Scale | Self-serve in dashboard, or by request to `support@roam-code.com` |
| Audit-trail records (no source) | Same as verdict envelope | Same |
| Operational logs (path + sha + verdict; no source content) | 30 days | Not configurable on Starter; SLA-defined on Business+ |
| Billing data | 7 years (GDPR + Greek tax law) | Not configurable; legal retention |
| Account metadata (org name, owner email) | Lifetime of subscription + 30 days post-cancellation | Deleted on written request after cancellation |
| **Verdict envelope + audit-trail post-cancellation** | Lesser of remaining tier-retention window or 90 days, then permanently deleted | Customer may request earlier deletion or export at any time |

Customers may request export of retained metadata at any time (machine-readable JSONL via `audit-trail-export`) and deletion at end of subscription via written request. Export is offered at least 30 days before any post-cancellation deletion takes effect.

---

## 6. No training, no retention of source content

Roam Code commits, contractually:

1. **No model training.** Customer source code, diffs, comments, identifier names, or metrics are never used to train, fine-tune, or improve any machine-learning model owned by Roam Code or shared with third parties. Inference-time use of customer-controlled AI features (where Customer explicitly enables and selects the model provider) is governed by Customer's own DPA with that provider; Roam Code does not retain prompts or completions beyond the analysis run.
2. **No source persistence beyond analysis.** The cloned working copy exists only for the duration of one analysis run (target p95 < 5 minutes) and is destroyed when the sandbox terminates. No backup snapshots are taken of the sandbox volume.
3. **No source in logs.** Operational logs include file paths (truncated to repo-relative), symbol names, line numbers, verdict scores, and run-IDs, but not function bodies, comments, or string literals.
4. **No source in metrics.** The metrics surfaced to your team (or via the API) are aggregated counts and scores. Source content is not part of any payload, dashboard, or notification.
5. **Encryption.** TLS 1.2+ in transit; AES-256 at rest. Sub-processor encryption configurations are listed at `roam-code.com/security`.

If any of those statements stops being true, customers receive at least 30 days notice and an opt-out path before the change takes effect. Material breaches trigger the incident-notification flow in §8.

---

## 7. Compliance posture

Roam Code makes the following claims; please verify against your own framework requirements.

| Framework | Status | Evidence |
|---|---|---|
| GDPR | Applicable; we are a Processor for PR-content during analysis. EU-based controllers receive a signed DPA before access (`templates/legal/dpa.md`) | DPA + Privacy Policy |
| SOC 2 CC8.1 (change management) | **Not currently certified.** The audit-trail subsystem produces evidence artefacts (signed in-toto v1 attestations, exportable JSONL) that can be used as an input to a SOC 2 CC8.1 evidence package once Roam Code completes a SOC 2 Type II audit (roadmapped). Roam Code makes no representation that the artefacts alone satisfy CC8.1 — your auditor judges that. | `roam audit-trail-export` output |
| ISO 42001 (AI management system) | **Not currently certified.** Roam's structural-review records and signed attestations are documentation/monitoring inputs that customers may use in their own ISO 42001 evidence package. Standalone Roam-Code certification not in scope at launch. | `roam audit-trail-conformance-check` output |
| ISO 27001 | **Not currently certified.** Roadmapped post-Series A. | — |
| EU AI Act Article 12 | Article 12 logging applies only to providers of high-risk AI systems listed in Annex III. Code-generation tooling is not in Annex III, so Roam Review is out of scope of Article 12. For customers whose *own product* is in Annex III, Roam's signed review attestations are usable as Article 14 human-oversight evidence — not Article 12 logs themselves. | `roam article-12-check` (scoping/readiness assessment, not certification) |

We do not make compliance claims we cannot evidence. If a framework is missing from the table and you need it, ask.

---

## 8. Incident response

- **Contact**: `security@roam-code.com` (PGP key published at `roam-code.com/.well-known/security.txt`).
- **Acknowledgement target**: 1 business hour for the security inbox, 24 hours for general support.
- **Notification**: customers affected by a confirmed security incident are notified within 72 hours of confirmation, in line with GDPR Article 33 timelines.
- **Status page**: `roam-code.com/status` shows live availability and incident history.
- **Right to audit**: Business and Scale tier customers may request, once per year on 30 days notice:
  - A remote SOC 2-style controls walkthrough (~2 hours)
  - The executive summary of the latest third-party penetration test (no raw findings)
  - The SBOM + cosign attestation for the deployed image
  Scale-tier customers may additionally request, under mutual NDA, in-person inspection of the operational environment and the raw penetration-test report.
  Starter and Team tier customers may request a written response to the SIG-Lite or CAIQ questionnaire (delivered within 10 business days) in lieu of a controls walkthrough.

---

## 9. SLA snapshot

The table below shows the **GA targets**. Pre-GA (during the design-partner programme) all SLAs are **best-effort**, with no service credits — pilot customers are remediated with a free month for any tier-grade incident. SLAs become contractually binding 90 days after the published GA date, once a trailing-12-month uptime sample is published at `roam-code.com/status`.

| Tier | Uptime target (GA) | p95 webhook-to-comment (GA target) | Support response | Credits (GA) |
|---|---|---|---|---|
| Starter | 99.0% | 90 seconds | 2 business days, email only | None |
| Team | 99.5% | 60 seconds | 1 business day, email only | 5% / 0.5pp under target |
| Business | 99.9% | 30 seconds | 4 business hours, email + Slack-shared channel | 10% / 0.5pp under target |
| Scale | 99.95% | 30 seconds | 1 business hour, named technical contact | 20% / 0.5pp under target |

Trailing-12-month uptime data is published at `roam-code.com/status` from GA + 90 days; SLAs become contractually binding on tier upgrade after that data is public. Until then, the values above are design targets, not commitments.

---

## 10. Quick-reference Q&A

> **Where is data hosted?**
> EU by default (`[CLOUD_PROVIDER]`, `[REGION]`). US opt-in is available; customers select region at install time.

> **Do you have SOC 2?**
> Not yet. The audit-trail subsystem is built to slot into a SOC 2 evidence package once we begin the audit programme. Self-Hosted ships the same controls in your network for customers that need certification today.

> **Can you sign our standard infosec questionnaire (CAIQ / SIG / VSAQ)?**
> Yes for Business and Scale customers; the response template is at `templates/legal/sig-lite-response.md` (v1 draft).

> **Can you run on-prem or in our VPC?**
> Yes — that's what Roam Self-Hosted is. Helm chart + Docker compose, license-keyed, your own Postgres, your own object storage, no callbacks.

> **What happens if you go away?**
> The OSS CLI (`pip install roam-code`, Apache 2.0) gives you the same engine on your machine, indefinitely. Your audit trail is exportable as JSONL on demand. There is no proprietary lock-in at the engine layer.

> **Right to delete?**
> Subscription metadata is deleted within 30 days of written cancellation request. Verdict envelopes and audit-trail records are retained for the lesser of the remaining tier-retention window or 90 days post-cancellation, then permanently deleted (see §5). Customers are offered an export at least 30 days before any post-cancellation deletion. Operational logs roll over per the retention table in §5. Billing records are retained per legal requirement (7 years).

---

## 11. Attestation

Where this packet is bundled with `roam audit-evidence-pack` output for a specific repository, the bundle includes a Cosign / Sigstore signature so the receiving security team can verify the JSON metadata, the audit-trail file, and the conformance score have not been tampered with in transit. Verification command and key are documented at `roam-code.com/security`.

---

## 12. Operational security

| Control | Status |
|---|---|
| **Penetration testing** | Annual third-party penetration test scheduled from GA. Executive summary available to Business+ customers under NDA; Scale-tier customers may request the raw report under mutual NDA. |
| **Vulnerability disclosure policy** | Public VDP at `roam-code.com/.well-known/security.txt`. 90-day disclosure timeline. PGP key for encrypted reports published at the same URL. Safe-harbour commitment for good-faith research. |
| **Bug bounty** | Not currently. VDP-only. Reassessed once paid-customer count reaches 100. |
| **SBOM (software bill of materials)** | Published per release in CycloneDX format, cosign-signed. Linked from the PyPI release page. CI pipeline emits in-toto v1 attestations for the build. |
| **Dependency hygiene** | Dependabot enabled on the public repo; daily security-advisory scan; high-severity advisories patched within 7 days of public disclosure. |
| **MFA enforcement** | All employee access to production systems (cloud provider, source-control, observability, customer data) requires hardware-key (WebAuthn / FIDO2) MFA. Password-only access is disabled. |
| **Background checks** | All engineering staff with production-data access undergo a third-party background check (`[VENDOR]`) before access is granted. Annual re-attestation. |
| **Backups + RPO / RTO** | Postgres daily backups, AES-256 encrypted at rest, retained 30 days. Object storage versioned + immutable for 7 days. RPO 24h / RTO 4h on Business; RPO 1h / RTO 1h on Scale. |
| **Code review of Roam itself** | Mandatory two-person review on every change to the Roam Review codebase. The roam-code CLI is dogfooded against itself in CI (`.github/workflows/dogfood.yml`); structural-risk verdicts on Roam's own PRs are public artefacts. |
| **Incident drills** | Quarterly tabletop exercises against the failure modes in §2; outcome summaries available to Business+ on request. |

`[VENDOR]` placeholder above is replaced with the concrete background-check provider in evaluation packets shared after a mutual NDA is in place.

---

## Cross-references

- DPA: [`dpa.md`](dpa.md) (rewrite for Roam Review before binding execution)
- SOW master: [`sow-master.md`](sow-master.md)
- Refund policy: `roam-code.com/refund`
- Privacy policy: `roam-code.com/privacy`
- Terms: `roam-code.com/terms`
- Public security policy: `roam-code.com/security`
- Status page: `roam-code.com/status` (planned)
- Live pricing: <https://roam-code.com/pricing>
