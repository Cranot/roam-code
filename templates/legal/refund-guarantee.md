# Refund and re-do guarantee

Two parts: the **public-facing copy** that lives on the commercial page, and the **internal SOP** that runs the policy.

> ⚠️ **REVIEW BEFORE USE.** v1 draft, 2026-05-05. Have a qualified attorney sanity-check the public copy and the refund-trigger criteria before promising this in writing.

---

## Public-facing copy (for roam.consulting)

Use this block on the commercial page below the pricing table and inside the SOW Section 11.

> **30-day refund guarantee.** If the audit doesn't surface 5 or more actionable findings, request a full refund within 30 days of delivery. No questions, no hoops. We can also re-run the audit at no charge once you've changed the codebase — your call.

Add this footnote / FAQ entry on the same page:

> **What counts as an "actionable finding"?** A specific, file-or-symbol-level recommendation that you could implement — e.g. *"Remove dead exports in `lib/utils.js` lines 124-198 (47 lines, 0 references)"* or *"Split god-component `OrderManager` (340-line class, 12 dependencies) per the proposed boundary in the report"*. Generic advice — *"add more tests"* — does not count.

Keep this language consistent across the website, SOW Section 11, the discovery-call closer email, and any social proof referencing the policy.

---

## Internal SOP (not customer-facing)

### When a refund request comes in

1. **Acknowledge within 24 hours** with a short reply: *"Got it — taking a look, will respond by `[NEXT_BUSINESS_DAY+1]`."* Don't argue, don't justify, don't escalate.

2. **Re-read the report** and count actionable findings against the policy criterion. Categories that count:
   - Dead-code SAFE-bucket entries with file paths and line ranges.
   - Risk-finding rows in Section 3 with specific file paths and root-cause notes.
   - Bus-factor items in Section 5 with specific directories and risk levels.
   - Suggested-CI-gate recommendations in Section 8 that map to a specific `roam` command.
   - Suggested-CLAUDE.md drop-in if the Client did not previously have one and would benefit.
   - Roadmap items in Section 9 that name specific files / commits / commands.

   Categories that do NOT count:
   - Generic statements ("write more tests", "split this big class").
   - Boilerplate text.
   - Auto-filled scorecard rows (those are signals, not findings).

3. **If 5+ actionable findings exist**: ask one clarifying question on a 15-min call before refunding. *"Curious which sections didn't land — was it the report itself, or how it mapped to your team's priorities?"* Use the answer to improve the next deliverable. After the call, if the Client still wants the refund, process it within 7 calendar days. No signing of additional waivers.

4. **If <5 actionable findings exist**: own the gap. Refund within 7 calendar days. Do a personal post-mortem on the engagement: was the codebase too small for the tier? Were there scope ambiguities? Did `roam audit` produce thin output? Adjust delivery process before the next audit.

5. **In all cases**: refund the **full** fee paid for the audit tier, minus any irrecoverable third-party fees (Stripe processing, currency conversion). Refund Rollout add-ons separately if that work was started — pro-rata against business days elapsed.

### How to actually issue the refund

- **Stripe Checkout (Indie / Standard)**: Stripe Dashboard → Payments → Find the charge → Refund (full or partial).
- **Stripe Invoicing (Enterprise)**: void the unpaid second invoice; refund the first invoice's payment in the dashboard.
- **Other payment rails (Wise, bank transfer)**: send refund within the same banking week.

Confirm refund completion to the Client by email within 1 business day of issue.

### Tracking

Keep a private log in `private/refund-log.md` (do **not** commit to public repo) with one row per refund:

| Date | Client | Audit tier | Fee paid | Reason | Lessons |
|---|---|---|---|---|---|
| `YYYY-MM-DD` | `[CLIENT]` | `[TIER]` | `$[FEE]` | `[REASON]` | `[LESSON]` |

If refund rate exceeds **20%** in any rolling 90-day window, halt outbound outreach and review:

- Are findings being delivered consistently? (Check audit-script output completeness.)
- Are clients being properly qualified at discovery? (Check pre-call screening question.)
- Is the deliverable structure landing? (Check walkthrough call satisfaction.)

Per the plan's pre-mortem 1.11 — refund rate is the leading indicator that the deliverable is generic. Address it at the deliverable level, not by softening the guarantee.

### What NOT to do

- Don't argue findings count line-by-line on the call.
- Don't refuse refunds outside the 30-day window — escalate to a "let's talk" instead.
- Don't make refund a condition of NDA or a non-disparagement waiver. Greptile-style hard-asks tank reputation faster than the refund itself.
- Don't quietly tighten the guarantee language after a refund. Keep it generous; if the metric needs adjustment, change the audit process, not the promise.
