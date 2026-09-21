---
name: prompt-optimizer
description: Optimize a vague subagent brief into a structured Agent task brief carrying falsifier, invariant, source-citation rule, and concrete-noun anchors. Invoke before Agent tool dispatch (Explore / general-purpose / Plan), before drafting research-wave specs, before writing HANDOVER memos, before composing /loop self-prompts. Source: agi-in-md Comparator-Aware Prompt Diagnostics paper + 81-prism Twinks catalog.
---

# Prompt Optimizer

A structured-assignment generator for subagent dispatch. The brief is
the dominant variable for subagent quality, not the model (agi-in-md
LAW 1). This skill forces every dispatch through a stable 6-field shape
so subagents return source-grounded findings instead of summary-mode
paragraphs.

## When to invoke

- Before any Agent dispatch (Explore / general-purpose / Plan)
- Before drafting a `dev/SPEC-*.md` research wave
- Before writing a HANDOVER memo a future agent reads cold
- Before composing a `/loop` self-pacing prompt

Skip for maximally-specific requests (one-symbol fact lookup,
single-line continuation, conversational reply with full context already
in window).

## Three rules

1. **Preserve the vanilla brief verbatim.** Copy the original first.
   Normalize only path quoting. Without the vanilla as comparator, A/B
   scoring is meaningless.

2. **Apply the 6-field template.** Load
   `references/optimized-prompt-template.md` and fill every field:
   Task, Target, Grounding rules, Process, Final output, Falsifier.
   Anchor every claim on concrete-noun terminals per LAW 4 (see
   `roam-code/CLAUDE.md` "Concrete-noun anchor vocabulary" for the
   accepted set).

3. **Iterate v1 → v2 → final.** Each pass names one ornament removed
   or one anchor sharpened. Stop when the next edit would not change
   the next agent's action (CLAUDE.md maintenance contract:
   Behavioral Coverage × Token Cost = Constant).

## Roam-code dispatch matrix

| Subagent | Brief shape | Falsifier example |
|---|---|---|
| Explore | breadth (`quick` / `medium` / `very thorough`) + exact symbol or pattern | "If you do not find X, that is the answer — do not invent it" |
| general-purpose | file paths + line numbers + exact API up front | "Every claim cites `file:line`; unsourced claims get deleted" |
| Plan | trade-off matrix + rejected alternatives + chosen path with rationale | "Name two rejected paths; if none, design space was too narrow" |

When dispatching ≥4 agents in parallel (memory:
`feedback_continuous_saturation_refill_to_four`), optimize each brief
independently — subagents share no context.

`claude` subagent is BANNED on this host (memory:
`feedback_no_claude_subagent`, W1072 worktree-MAX_PATH); route every
optimized brief to Explore / general-purpose / Plan.

## Epistemic tags

Every claim in the optimized brief carries one tag:

- `[SOURCE]` — directly read from file/log/output (maps to roam
  `direct` confidence)
- `[DERIVED]` — computed from sources (maps to `derived`)
- `[ASSUMED]` — working hypothesis pending verification (maps to
  `inferred`)
- `[UNVERIFIABLE]` — out-of-scope or unprovable from artifacts at hand
  (maps to `legacy_fallback`)

Canonical vocabulary: `src/roam/evidence/_vocabulary.py` →
`CLAIM_CONFIDENCES` (4-member closed enum).

## Output shape

For a rewrite return:
- `vanilla` — original brief verbatim
- `optimized_final` — dispatch-ready text
- `iteration_notes` — concise v1 / v2 / final diff rationale
- `measurement_plan` — 8-axis scoring plan when A/B was requested

For an A/B return:
- Dispatch parameters (subagent_type, working dir, identical context)
- 8-axis comparison from `references/optimized-prompt-template.md`
- Final opinion + caveats

## Reference

`references/optimized-prompt-template.md` carries the 6-field skeleton,
the Codex CLI cross-family pattern, the 8-axis scoring rubric, and the
documented failure modes.

## Upstream

Source: `agi-in-md/.agents/skills/prompt-optimizer/`. Empirical backing:
the 81-prism catalog with 22 top-tier "Twinks" scored on production
code + the Comparator-Aware Prompt Diagnostics paper. The 12 agi-in-md
laws are already imported into `roam-code/CLAUDE.md`; this skill
operationalizes them at dispatch time.
