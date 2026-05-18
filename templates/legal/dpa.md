# Data Processing Agreement

> REVIEW BEFORE USE. v2 draft, 2026-05-18. This template is aligned to the
> current PR Replay service and the planned Roam Review hosted service, but it
> is not legal advice and must be reviewed by qualified counsel before binding
> execution. Bracketed fields must be completed per engagement.
>
> Research references checked on 2026-05-18: GDPR Article 28 processor
> structure, the European Commission controller-processor standard contractual
> clauses, Stripe Payment Links / invoicing docs, and AADE myDATA public
> updates. Recheck these before external use.

---

This Data Processing Agreement ("DPA") forms part of and is subject to the
Statement of Work, order form, or subscription agreement signed on
`[EFFECTIVE_DATE]` (the "Agreement") between:

> For PR Replay engagements, the "Agreement" is the Statement of Work at
> `https://github.com/Cranot/roam-code/blob/main/templates/legal/sow-pr-replay.md`
> (the "SOW"). The SOW incorporates this DPA by reference. In case of
> conflict, **Section 13 (Order of precedence)** applies.

- **`[CONTROLLER_LEGAL_NAME]`** ("Controller" / "Client"); and
- **`[PROCESSOR_LEGAL_NAME]`** ("Processor" / "Provider").

This DPA applies when Processor processes Personal Data on Controller's behalf
in connection with the services described in the Agreement.

---

## 1. Definitions

Terms used in this DPA - including "Personal Data", "Data Subject",
"Processing", "Controller", "Processor", "Sub-processor", and "Supervisory
Authority" - have the meanings given in Regulation (EU) 2016/679 (the "GDPR").
"Applicable Data Protection Law" means the GDPR and applicable supplementary
law, including UK GDPR where relevant and Greek Law 4624/2019 where applicable.

## 2. Processing description

| Aspect | PR Replay | Roam Review / hosted services |
| --- | --- | --- |
| Subject matter | Read-only replay of agreed pull-request history to produce a structural-review report. | Planned pre-merge structural review, dashboards, and evidence history for subscribed repositories. |
| Duration | Agreement term plus the retention period in Section 7. | Subscription term plus the retention period in Section 7. |
| Nature | Local deterministic analysis using `roam-code`; temporary repo clone or git bundle; no source-code modification. | Git provider webhook ingestion, PR diff analysis, Roam evidence generation, dashboard/report storage where enabled. |
| Purpose | Deliver the PR Replay report, walkthrough, and agreed follow-up. | Deliver Review verdicts, evidence packets, dashboards, and support. |
| Data subjects | Client employees, contractors, contributors, and any person whose Personal Data appears in git metadata, pull requests, comments, fixtures, test data, or source files. | Same as PR Replay, plus users/admins of the hosted service. |
| Personal Data categories | Names, email addresses, usernames, commit metadata, PR metadata, comments, access logs, and any Personal Data inadvertently committed to source code or fixtures. | Same as PR Replay, plus account, billing-contact, organization, webhook, usage, and support metadata. |

Hosted Roam Review fields are included so the template can evolve with the
product. Do not use this DPA for hosted Review until the hosting provider,
sub-processors, retention windows, and security schedule are completed.

## 3. Processor obligations

Processor will:

1. Process Personal Data only on Controller's documented instructions,
   including this DPA, the Agreement, and written instructions sent by
   Controller.
2. Inform Controller if Processor believes an instruction infringes Applicable
   Data Protection Law, unless prohibited by law.
3. Ensure that personnel authorised to process Personal Data are bound by a
   contractual or statutory confidentiality duty.
4. Implement appropriate technical and organisational measures proportionate to
   the risk, including the measures in Section 6.
5. Assist Controller, where reasonably possible, with data-subject requests
   under Articles 15-22 GDPR.
6. Assist Controller with Articles 32-36 GDPR obligations, including security,
   breach notification, DPIAs, and prior consultation where the requested
   assistance relates to Processor's processing.
7. Make information reasonably necessary to demonstrate compliance available to
   Controller and support audits under Section 10.
8. Delete or return Personal Data at the end of the services as described in
   Section 7.

## 4. Controller obligations

Controller will:

1. Provide lawful instructions and ensure it has a valid legal basis for the
   Personal Data supplied to Processor.
2. Avoid sharing production secrets, credentials, customer records, or special
   category data unless strictly necessary for the agreed scope.
3. Prefer redacted git bundles or least-privilege read-only repository access
   where that still allows Processor to deliver the service.
4. Notify Processor promptly if the repo or materials contain unusually
   sensitive data that changes the risk profile.

## 5. Sub-processors

Controller authorises the following Sub-processors at the Effective Date:

| Sub-processor | Purpose | Personal Data scope | Location / transfer basis |
| --- | --- | --- | --- |
| Stripe, Inc. | Payment processing, receipts, refunds, and billing records. | Billing contact and transaction metadata only; no source code. | USA; Stripe transfer terms/SCCs where applicable. |
| GitHub, Inc. | Repository access when Controller chooses GitHub collaborator, deploy-key, GitHub App, or webhook access. | Repository content, git metadata, PR metadata, and user metadata needed for the agreed service. | USA / global; GitHub transfer terms/SCCs where applicable. |
| `[TBD: HOSTING_PROVIDER_IF_ANY]` | Hosted Roam Review or Cloud infrastructure, if enabled. Not applicable to PR Replay (local-only). | `[TBD: DATA_SCOPE]` | `[TBD: REGION_AND_TRANSFER_BASIS]` |
| `[TBD: EMAIL_OR_STORAGE_PROVIDER_IF_ANY]` | Delivery of reports, support, or encrypted artifact storage, if used. For PR Replay v1 delivery is via direct email from Processor's mailbox. | Contact details and report artifacts. | `[TBD: REGION_AND_TRANSFER_BASIS]` |

Processor will not add a new Sub-processor without at least 14 calendar days'
prior notice, unless emergency replacement is necessary to maintain security or
service continuity. Controller may object on reasonable data-protection
grounds. If the parties cannot resolve the objection, Controller may terminate
the affected service and receive any refund required by the Agreement.

Processor remains responsible for Sub-processor performance of equivalent data
protection obligations.

## 6. Security measures

For PR Replay, Processor will apply at least the following measures:

- **Local-only analysis.** `roam-code` is a 100% local CLI; it requires
  no API key, no vendor cloud endpoint, and no inbound network egress
  to Processor or any third party for analysis. Personal Data and source
  code stay on Processor's local working machine for the duration of the
  engagement and never transit a Processor-operated cloud service.
  Sub-processor scope (Stripe, GitHub) is limited to the purposes listed
  in **Section 5** and does not include analysis data.
- Temporary working copy stored only on encrypted storage controlled by
  Processor.
- Client repositories excluded from consumer cloud-sync folders.
- Read-only repository access wherever possible.
- No training, fine-tuning, evaluation, or benchmarking on Client code,
  diffs, comments, metrics, or derived artifacts.
- OS auto-updates enabled, endpoint malware protection active, and screen lock
  under 5 minutes idle.
- Engagement ledger recording tier, client, scope, commits scanned, generated
  report path, and deletion status.
- Report artifacts stored encrypted at rest until deletion or retention expiry.

For hosted Roam Review or Cloud, Processor must complete a separate security
schedule before launch covering hosting region, encryption, access control,
key management, logging, backup, vulnerability handling, incident response,
retention, and admin access review.

## 7. Return, deletion, and retention

Unless the Agreement states a shorter period, Processor will:

1. Delete temporary repository clones, git bundles, indexes, and derivative
   working files within 7 calendar days after report delivery or service
   termination.
2. Provide written deletion confirmation on request.
3. Retain the final delivered report for up to 90 days to support follow-up
   questions, unless Controller requests earlier deletion.
4. Retain engagement ledger entries for up to 2 years for audit defence,
   reconciliation, and dispute handling.
5. Retain invoices, accounting records, and legally required tax records for
   the period required by applicable law, including Greek tax requirements
   where Processor is Greek-established.
6. Retain only anonymised aggregate product metrics where no Client, repo,
   contributor, code, identifier, or quote can be reconstructed.

Hosted service retention must be filled in the relevant order form before
launch. Placeholder hosted retention language is not sufficient for external
execution.

## 8. Optional AI providers

By default, Processor will not transmit Client source code, diffs, comments, or
report artifacts to Anthropic, OpenAI, Google, or any other AI provider.

Any AI-assisted narrative summarisation or third-party model use requires a
separate written opt-in that names the provider, data categories, purpose,
retention setting, transfer basis, and revocation process. The provider must
also be added to Section 5 before processing begins.

## 9. Personal Data breach notification

Processor will notify Controller without undue delay, and where feasible within
72 hours after becoming aware, of a Personal Data breach affecting Controller
Personal Data. The notice will include, to the extent known:

- Nature of the breach.
- Approximate categories and number of Data Subjects and records affected.
- Likely consequences.
- Measures taken or proposed to address the breach and mitigate effects.

Processor will reasonably assist Controller with Articles 33-34 GDPR
notifications where the breach relates to Processor's processing.

## 10. Audits

Once per year, with at least 30 days' written notice, Controller may audit
Processor's compliance with this DPA. Audits must be limited to information
relevant to this DPA, conducted during business hours, subject to reasonable
confidentiality safeguards, and structured to avoid unreasonable disruption.

Where a material non-compliance finding is confirmed, Processor will remediate
within a reasonable period and bear reasonable audit costs directly caused by
the confirmed non-compliance. Otherwise, Controller bears its audit costs.

## 11. Cross-border transfers

Processor's primary PR Replay processing location is Greece (EU), unless the
Agreement says otherwise. Where Personal Data is transferred outside the EEA,
the parties will rely on an applicable GDPR Chapter V safeguard, such as an
adequacy decision, the European Commission Standard Contractual Clauses, or
another lawful transfer mechanism.

Where Controller requests a non-EEA hosted deployment, Controller and Processor
will document the transfer basis and supplementary measures in the order form
or security schedule.

## 12. Liability

Each party's liability under this DPA is subject to the liability provisions in
the Agreement, except where Applicable Data Protection Law requires otherwise.

## 13. Order of precedence

In case of conflict between this DPA and the Agreement, this DPA prevails only
for matters concerning Processor's processing of Personal Data.

## 14. Term

This DPA continues for as long as Processor processes Controller Personal Data
under the Agreement. Sections 7, 9, 10, 11, 12, and 13 survive termination for
as long as needed to give them effect.

---

## Signatures

**Controller - `[CONTROLLER_LEGAL_NAME]`**

- Signed: ____________________
- Name: `[CONTROLLER_SIGNATORY]`
- Title: `[CONTROLLER_TITLE]`
- Date: ____________________

**Processor - `[PROCESSOR_LEGAL_NAME]`**

- Signed: ____________________
- Name: `[PROCESSOR_SIGNATORY]`
- Title: `[PROCESSOR_TITLE]`
- Date: ____________________
