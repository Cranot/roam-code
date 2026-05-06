# USPTO Trademark Section 1(b) — filing checklist

> ⚠️ **REVIEW BEFORE FILING.** v1 draft, 2026-05-05. Have an IP attorney sanity-check Class 9 stock description before filing if budget allows. Section 1(b) is intent-to-use; the alternative is Section 1(a) actual-use.

## What & why

- **Mark**: `roam-code` (literal mark, standard characters)
- **Class**: **9** (downloadable software)
- **Filing basis**: **Section 1(b)** — intent-to-use. We aren't yet selling at
  meaningful commercial scale, so 1(b) lets us reserve priority while we
  build out paid tier traffic. Convert to 1(a) actual-use within 6 months of
  first paid audit by filing a Statement of Use (additional $100 fee).
- **Filing fee** (2025-26 schedule): **$350** for TEAS-Plus single class.
  Confirm at <https://www.uspto.gov/trademarks/fees-payment-information/summary-2025-trademark-fee-changes>
  before filing.
- **Greek OBI** secondary filing: optional, ~€140. Skip until first paid
  EU customer asks for it.

## Pre-filing checklist

Run all of these **before** opening the TEAS form:

- [ ] **Search the TESS database** at <https://tmsearch.uspto.gov/> for
      conflicts. Variants to check: `roam`, `roam code`, `roamcode`. Note
      any live registrations in Class 9. Existing live `ROAM`-marks for
      navigation / mapping software are non-conflicting (different goods);
      Class 9 software is the relevant intersection.
- [ ] **Check Greek OBI** (`obi.gr`) for any prior Greek mark conflicts —
      same string, broader class screen.
- [ ] Confirm Apache 2.0 license grant is in `LICENSE` (already done — A.1).
      Trademark filing should happen AFTER the license-text change is
      committed to main, not before, so the public mark-bearing project is
      consistent.
- [ ] Decide owner: file in Provider's name (CosmoHac / Cranot personally,
      OR Stripe Atlas Delaware C-corp once incorporated). Default: file
      personally now; assign to Atlas C-corp later via TEAS Section 7
      assignment if Atlas incorporates within 12 months.
- [ ] Choose specimen-of-use storage location: keep a screenshot of the
      roam-code GitHub README (showing the trademarked name in commercial
      context with `™` after first use), the PyPI page, and the
      roam.consulting landing page when it goes live. Saves time when
      converting 1(b) → 1(a).

## TEAS-Plus form fields (rough)

When filing at <https://teas.uspto.gov/forms/standard-character-claim>:

| Field | Value |
|---|---|
| **Mark literal element** | `roam-code` |
| **Mark drawing type** | Standard characters |
| **Owner** | `[Cranot full legal name]` (individual) OR `[Atlas C-corp name]` |
| **Owner address** | `[Greek address with full postal code]` |
| **Citizenship / state of incorporation** | Greece (individual) or Delaware (C-corp) |
| **Filing basis** | Section 1(b) — intent to use |
| **International class** | 009 |
| **Goods / services description** | See standard-description text below |
| **Specimen of use** | Not required for 1(b); supplied later in Statement of Use |
| **Translation** | None (mark is in standard English characters) |
| **Disclaimers** | None expected; "code" is descriptive but the compound `roam-code` is suggestive overall |
| **Email** | `[Provider email]` |
| **Authorisation** | Signature line below TEAS-Plus declaration |

### Goods / services description (Class 9 standard text — copy-paste)

> Downloadable computer software for static code analysis, codebase
> indexing, dependency mapping, architectural diagnostics, and integration
> with AI coding assistants; downloadable computer software for measuring
> code quality, technical debt, and code health metrics in software
> repositories; downloadable computer software for generating code
> intelligence reports for software development teams.

(This is intentionally narrow to Class 9 software downloadable goods and
avoids triggering Class 42 SaaS-services which would require an additional
$350 filing fee and isn't relevant until SaaS launches.)

## Post-filing tracking

After filing receipt:

- [ ] Save **serial number** (assigned within minutes of filing) and
      receipt PDF to `private/uspto-trademark/`.
- [ ] Set 90-day calendar reminder to check examiner status (TSDR).
- [ ] If office action issued: respond within 6 months. Most common is
      identification clarification — straightforward.
- [ ] On publication: 30-day opposition period. Watch the Trademark
      Official Gazette publication date.
- [ ] If no opposition: receive **Notice of Allowance** ~10 weeks after
      publication. Then 6 months to file Statement of Use (or pay $125 +
      file Extension Request — up to 5 extensions × 6 months = 3 years
      maximum to actual use).

## After registration

- [ ] Add `™` mark to the `roam-code` README, the `roam.consulting`
      landing page, all marketing material. Switch to `®` after Statement
      of Use is accepted.
- [ ] Create `TRADEMARK.md` at repo root with this attestation:
      ```
      "roam-code" is a trademark of [OWNER NAME]. Permitted uses include
      describing the project, contributing, and integrating roam-code into
      your tools. Disallowed uses: commercial competing products called
      "roam-code", endorsement implications, or use that suggests official
      affiliation without written permission. Apache 2.0 license grant on
      the source code does NOT include a trademark license.
      ```
- [ ] Renewal at year 5-6 (Section 8 declaration, ~$225) and year 9-10
      (combined Section 8 + 9 declaration, ~$425). Calendar reminders.

## What this does NOT cover

- **Logo / wordmark with stylisation**: requires a separate TEAS application
  with the design code. File later if a logo emerges.
- **Class 42 SaaS-services**: file ONLY when SaaS plane (roam Sentinel)
  launches with paid traffic.
- **EU-wide protection**: needs an EUIPO filing or Madrid Protocol designation.
  Defer until first EU paying customer asks for it.

## Action right now (Phase 1)

Mark task #9 in_progress when ready, work through pre-filing checklist, then
file. Total time-on-task: ~30 minutes once searches are done. Filing fee:
$350. Wallclock to allowance: ~6 months.

Filing is **NOT a launch blocker** for Phase 3. Do it in parallel with
Phase 1 production.
