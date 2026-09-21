# Optimized Prompt Template

## 6-field skeleton (copy-paste, fill brackets, delete sections that do not apply)

```text
Task: [concrete assignment — one imperative sentence].

Target:
- Artifact: [file, folder, repo area, paper, issue, command, surface]
- Scope: [what is in bounds]
- Out of scope: [what to ignore]
- Permission: [read-only / may edit / may run tests / no network]

Grounding rules:
- Cite source evidence from [files / docs / logs].
- Use file paths and line numbers for code claims.
- Tag each claim [SOURCE], [DERIVED], [ASSUMED], or [UNVERIFIABLE].
- Separate extraction artifacts, generated mutations, and benchmark
  fixtures from real defects.

Process:
1. Build a short structural model of the target before listing
   conclusions.
2. Trace the key symbol, data flow, control flow, or decision path
   that determines correctness.
3. State one central falsifiable claim plus the strongest evidence
   that would refute it.
4. State one invariant or law that should hold if the design is
   correct.
5. Improve the first pass once, then write the final answer.

Final output:
- Structural model / mental map.
- Central claim + falsifier.
- Findings or recommendations, ordered by severity or impact.
- Verification hooks: exact tests, commands, or checks the reader
  runs to confirm or disprove the result.
- Caveats and assumptions.
```

## Roam-code Agent-tool A/B pattern

Send two parallel `Agent` calls with identical settings except `prompt`:

```
Agent A — subagent_type: <Explore | general-purpose | Plan>
         prompt: <vanilla brief verbatim>

Agent B — subagent_type: <same as A>
         prompt: <optimized brief filled from skeleton above>
```

Run both `run_in_background: true` (memory:
`feedback_loop_async_jit_replenishment`). Score outputs on the 8 axes
below.

## Codex CLI cross-family pattern

For Claude-vs-GPT adversarial scoring (the `research-auto` discipline
from agi-in-md — disagreement IS the calibration signal):

```powershell
codex exec -m gpt-5.5 -c model_reasoning_effort=xhigh --cd "<repo>" `
  --sandbox read-only --ephemeral --color never `
  -o output\<run>\vanilla_output.md "<vanilla prompt>"

codex exec -m gpt-5.5 -c model_reasoning_effort=xhigh --cd "<repo>" `
  --sandbox read-only --ephemeral --color never `
  -o output\<run>\optimized_output.md "<optimized prompt>"
```

## 8-axis scoring rubric

Score each output 0–10 or coarse high/mid/low:

1. **Useful findings** — count of source-grounded, non-trivial findings.
2. **False positives** — count of claims failing source-verification.
3. **Source-grounded claim ratio** — fraction of claims with
   `file:line` citations.
4. **Falsifier quality** — does the falsifier actually refute the
   claim, or is it cosmetic?
5. **Verification hooks** — count of exact tests / commands / checks
   the reader can run.
6. **Handling of extraction artifacts** — does it separate
   generated / mutated / fixture code from real defects?
7. **Invariant strength** — is the stated invariant load-bearing or
   generic?
8. **Word count + operational cost** — penalize bloat; reward density.

## Failure modes (documented honestly)

Optimizer cost exceeds benefit on:

- **One-line fact lookups** ("what does `_finding` return?") —
  optimizer overhead exceeds answer length.
- **Conversational continuations** — full prior context already in
  window; structural reframing wastes tokens.
- **Already-narrow scope** — single-symbol fact lookups; the
  `file:line` IS the source.

Rule: optimize when drift is plausible. Maximally-specific requests
skip the optimizer.

## Roam-code-specific falsifier patterns

When the dispatched task is a debug / test-failure / hypothesis
investigation, prepend this falsifier to the brief (per CLAUDE.md
"Re-run before declaring a fix" discipline rule, W978 / W851 / W1005):

```text
Falsifier: re-run the failing test in isolation (`pytest <path> -n 0`)
before hypothesising. If it passes solo but fails under `-n auto`, the
cause is test-isolation (sibling-test side effect, autouse conftest
leak, shared filesystem state) — NOT the assertion. Confirm with one
targeted re-run before declaring the fix.
```

When the dispatched task is a "duplicated to avoid cycle" cleanup
audit (per CLAUDE.md "Verify the cycle before hedging", W907):

```text
Falsifier: grep both directions for the alleged import edge before
trusting the docstring. If the cycle does not actually exist, the
duplication is cargo-cult and should hoist to a shared module.
```
