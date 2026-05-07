# Data Processing Agreement

> ⚠️ **REVIEW BEFORE USE.** v1 draft, 2026-05-05. Based on the GDPR Article 28 framework. Have a qualified attorney review before binding execution. EU-based clients should require this signed before repo access.
> Superseded for launch until rewritten. This DPA was drafted for the old audit
> service; update the service description, subprocessors, retention, and
> product-specific processing details before using it for PR Replay or Review.

---

This Data Processing Agreement ("**DPA**") forms part of and is subject to the Statement of Work signed `[SOW_DATE]` (the "**SOW**") between:

- **`[CONTROLLER_LEGAL_NAME]`** ("**Controller**" / "Client"); and
- **`[PROCESSOR_LEGAL_NAME]`** ("**Processor**" / "Provider").

This DPA applies to the Processor's processing of Personal Data on the Controller's behalf in connection with the services described in the SOW.

---

## 1. Definitions

Terms used in this DPA — "Personal Data", "Data Subject", "Processing", "Controller", "Processor", "Sub-processor", "Supervisory Authority" — have the meanings given in **Regulation (EU) 2016/679** (the "GDPR"). "Applicable Data Protection Law" means the GDPR and any national or supplementary laws (e.g. UK GDPR, Greek Law 4624/2019).

## 2. Subject matter, duration, nature, purpose

| Aspect | Detail |
|---|---|
| **Subject matter** | Static analysis of Controller's source code repository to produce an architectural audit report (the "Service") |
| **Duration** | The duration of the SOW plus the retention period in Section 7 |
| **Nature** | Read-only analysis using `roam-code` (Apache 2.0, local SQLite indexer); no modification of Controller's systems |
| **Purpose** | To deliver the Service per the SOW |
| **Categories of Data Subjects** | Controller's employees and contributors whose names appear in repository git history; Controller's end users only if their Personal Data appears in source code (e.g. test fixtures, hard-coded examples) |
| **Categories of Personal Data** | Author names and email addresses from git commit metadata; any Personal Data inadvertently committed to source code (e.g. fixture data, comments, test files) |

## 3. Processor obligations

The Processor will:

(a) Process Personal Data **only** on the Controller's documented instructions, including those in this DPA and the SOW. The Processor will inform the Controller if a legal requirement compels it to process otherwise.

(b) Ensure that personnel authorised to process Personal Data are bound by a contractual or statutory duty of confidentiality.

(c) Implement appropriate technical and organisational measures to ensure a level of security appropriate to the risk, including:

- **Encryption at rest**: Controller's repository is stored only on full-disk-encrypted volumes (BitLocker / FileVault / VeraCrypt) on the Processor's working machine.
- **No cloud sync**: client repositories are excluded from automatic cloud synchronisation services (OneDrive / iCloud / Google Drive) on the Processor's machine.
- **No third-party AI**: Processor's `roam-code` analysis tool runs 100% locally with zero API keys; Controller's source code is not transmitted to any AI provider unless the Controller explicitly opts in (see Section 6).
- **Access logs**: per-engagement log of `repo_id, file_accessed, timestamp, action` retained for the duration of the engagement plus the retention period.
- **Endpoint hygiene**: OS auto-updates enabled; full-disk antivirus active; screen lock under 5 min idle.

(d) Assist the Controller, by appropriate technical and organisational measures, in fulfilling its obligation to respond to Data Subject requests under Articles 15-22 GDPR.

(e) Assist the Controller in ensuring compliance with Articles 32 to 36 GDPR (security, breach notification, DPIAs, prior consultation).

(f) Make available to the Controller all information necessary to demonstrate compliance with this DPA, and allow for and contribute to audits — including inspections — conducted by the Controller or another auditor mandated by the Controller (see Section 9).

## 4. Sub-processors

The Controller authorises the following Sub-processors at the Effective Date:

| Sub-processor | Purpose | Location |
|---|---|---|
| Stripe, Inc. | Billing only (no source code or repository content) | USA |
| `[CLOUD_PROVIDER_IF_ANY]` | `[E.G. encrypted EU-region storage for git bundle uploads]` | `[REGION]` |

The Processor will not engage additional Sub-processors without prior written notice (at least **14 calendar days**) to the Controller. The Controller may object on reasonable data-protection grounds; if the Parties cannot resolve the objection, the Controller may terminate the SOW with refund of unworked prepayment.

The Processor is liable for Sub-processor compliance with this DPA equivalent obligations.

## 5. Cross-border transfers

If Personal Data is transferred outside the European Economic Area, the transfer will be subject to one of the safeguards in Articles 44-49 GDPR — typically the European Commission's **Standard Contractual Clauses** (2021/914/EU) annexed to this DPA on request. The Processor's primary processing location is **Greece (EU)**; transfers to US-based Sub-processors (e.g. Stripe) rely on those Sub-processors' own SCC commitments.

## 6. Optional AI-augmented analysis

By default, no Personal Data or source code is transmitted to any AI provider. The Controller may **opt in**, by separate written authorisation, to AI-summarised findings using the Anthropic API. If opted in:

- Source code chunks may be transmitted to Anthropic's API for the limited purpose of generating natural-language summaries.
- Anthropic's data-handling policy applies (see <https://www.anthropic.com/legal>).
- The Controller's authorisation is revocable at any time on written notice.

If not opted in, the Processor will use only deterministic local analysis.

## 7. Data return and deletion

Upon completion of the Service or earlier termination of the SOW, the Processor will:

(a) Delete the Controller's source code, repository working copies, and derivative working files within **14 calendar days**.
(b) Provide written confirmation of deletion within 7 calendar days of completion.
(c) Retain only:
   - The final audit report deliverable, in encrypted archive, for **90 days** to support reasonable Controller follow-up questions; the Controller may request earlier deletion.
   - Per-engagement access logs for **2 years** for compliance and audit defence.
   - Sanitised, fully Controller-anonymised metrics (file counts, language distribution, never identifiable code) for Processor's own product analytics.

(d) On Controller's request, extend retention by amendment for repeat or follow-up audits (up to 90 additional days).

## 8. Personal Data breach notification

The Processor will notify the Controller without undue delay, and in any event within **72 hours**, of becoming aware of any Personal Data breach affecting Controller Personal Data. The notice will include, to the extent known:

- Nature of the breach.
- Approximate categories and number of Data Subjects and records affected.
- Likely consequences.
- Measures taken or proposed to address the breach and mitigate effects.

The Processor will reasonably assist the Controller in fulfilling Articles 33-34 GDPR notifications.

## 9. Audits

Once per year, with at least 30 days' written notice, the Controller may audit the Processor's compliance with this DPA. The audit will be conducted during business hours, will not unreasonably interfere with the Processor's operations, will be limited to information relevant to this DPA, and will be subject to the NDA. Audits at the Processor's expense if material non-compliance is found; otherwise at the Controller's expense.

## 10. Liability and indemnity

Each Party's liability under this DPA is subject to the liability cap in the SOW, except for breaches of confidentiality and breaches resulting from gross negligence or wilful misconduct, which are uncapped.

## 11. Term and termination

This DPA continues for as long as the Processor processes Controller Personal Data under the SOW. Sections 7, 8, 9, and 10 survive termination.

## 12. Order of precedence

In case of conflict between this DPA and the SOW, this DPA prevails for matters concerning the Processing of Personal Data.

## 13. Governing law

This DPA is governed by the same law as the SOW.

---

## Signatures

**Controller — `[CONTROLLER_LEGAL_NAME]`**

- Signed: ____________________
- Name: `[CONTROLLER_SIGNATORY]`
- Title: `[CONTROLLER_TITLE]`
- Date: ____________________

**Processor — `[PROCESSOR_LEGAL_NAME]`**

- Signed: ____________________
- Name: `[PROCESSOR_SIGNATORY]`
- Title: `[PROCESSOR_TITLE]`
- Date: ____________________
