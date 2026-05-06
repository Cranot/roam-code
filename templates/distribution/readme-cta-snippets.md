# README Services CTA snippets

Drafted Phase-1, **held for Phase-3 coordinated launch**. Do not merge into the
public READMEs until the commercial page is live, the audit template is
exercised, and the Phase 2 readiness review has passed.

## When to merge

Trigger: Phase 3 coordinated launch (Sunday 22:00 PT / Monday 08:00 CET, the
day Show HN goes live). All four of these go live within the same 4-hour
window:

- D.3 — `roam-code` README CTA below
- D.4 — `claude-code-guide` README CTA below
- D.5 — awesome-list PR submissions
- C.3 — benchmark blog post publish

---

## D.3 — `roam-code` README CTA (above the fold)

Insert directly after the existing badges block in `README.md`, **before** the
"## What is Roam?" section. Replace the placeholder TL/DR, never touch the
core feature listing.

```markdown
> **Need an outside read on your codebase?**
> AI Agent Readiness Audits use roam-code to map an entire repo in 5–10 days
> and ship a 15-page report your team can act on Monday morning.
> $1.8K / $4.5K / $12K. → [roam.consulting/audit](https://roam.consulting/audit)
```

Notes:

- Single line, intentionally lower visual weight than the install instructions
  so OSS users don't feel paywalled.
- "Outside read" framing avoids stepping on Cursor/Cline/CodeRabbit naming.
- Pricing in the line itself filters tire-kickers without forcing a click.
- Domain has to exist and serve the page before this lands; otherwise dead
  link tanks trust.

## D.4 — `claude-code-guide` README CTA (above the fold)

The `claude-code-guide` README has 2.3K stars and ~5–15K weekly views — the
highest-leverage owned asset per `roam_code_plan_v1.md`. CTA goes near the
top, ideally directly under the project description.

```markdown
> **Running Claude Code on a real codebase?**
> If your team's been on Claude Code / Cursor for 6+ months and PRs are
> drifting, an [AI Agent Readiness Audit](https://roam.consulting/audit) maps
> what changed and where the agent stops being useful.
> Built with [roam-code](https://github.com/Cranot/roam-code) — same author.
> Indie / Standard / Enterprise from $1.8K.
```

Notes:

- Targets the persona — VP Eng / Platform / DevEx 6+ months in — directly.
- Connects the dots: claude-code-guide reader → roam-code → audit service.
- "Same author" is a credibility shortcut for first-time visitors.
- This block can be A/B tested against a tighter variant after first 30 days.

---

## Anti-patterns to avoid

- **No Stripe Checkout button on the README.** Click goes to the commercial
  page, where pricing context lives. The README is awareness, not conversion.
- **No "DM me" / @-handle CTA.** Filtering happens via Calendly screening
  question (E.1). Public DM-bait attracts low-intent contacts.
- **No "limited slots this month" urgency.** Real scarcity is fine; manufactured
  scarcity reads as desperate and tanks trust at the persona level.
- **Don't add the CTA to per-language docs / `.cursor/rules/` / MCP server
  card descriptions.** Those are technical surfaces; service pitches there
  feel intrusive and dilute the technical credibility.
