---
name: roam-agent-guidance
description: "Review and maintain the technical instructions Roam gives coding agents: shipped skills, tool descriptions, preset guidance and generated instruction blocks. Use for stale, unreachable or misleading usage guidance, not product persuasion, general repo exploration or runtime defect repair."
---

# Roam agent guidance

Make an instruction lead an agent to an available operation and a conclusion
the returned evidence can support. This is instruction maintenance, not a new
agent interface or another general hardening pass. A mixed document can contain
both a human adoption explanation and technical operating instructions; review
each for its own job rather than rewriting both as a pitch or a gate checklist.

## Find the instruction and its owner

Resolve the Roam checkout and read current `AGENTS.md`. Use
`docs/understanding-roam.md` for product and evidence meaning,
`docs/agent-cli.md` for invocation guidance and `docs/mcp-tools.md` for MCP
presets and inventory ownership. Read the relevant sections of
`docs/concepts/verification-evidence.md` and `docs/concepts/detector-evidence.md`
when the instruction asks an agent to act on findings. A frozen packet can
supply these authorities for a trial; missing sources remain missing.

Trace each changed instruction to its source: handwritten guidance, tool
docstring, maintained generator, or user-owned annotation. Inspect generated
markers and the current generator before editing. For example, the MCP inventory
names its owner in `docs/mcp-tools.md`; minimap's command documents its sentinel
block and durable notes input. Preserve unrelated notes and regenerate the owned
output when authorized. Do not invent a new universal surface map or hand-edit
generated copies because they are the first matching file.

Bind syntax and availability to the intended version, client, preset and shell.
Use the checkout's verified venv, preserving an explicitly requested launcher;
inspect actual help/tool schemas and save full JSON where acted on. A CLI flag
is not necessarily an MCP argument. A registered tool may be absent from the
connected preset; expansion guidance may require a server restart. Do not change
the user's configuration merely to make an example work. Help proves syntax,
not execution, coverage or supported behavior on another release.

Exercise disputed examples from the reader's intended state, not just Roam's
checkout. A clean committed CI checkout differs from an unstaged working diff;
a repository-relative rules directory or optional dependency may not exist after
the stated install. Use a small representative fixture to verify selection and
prerequisites, and disclose any stubbed boundary. Keep simple verified examples
short rather than adding every possible environment caveat.

## Make the next action usable

State a concrete operation and what its result is for. A discovery example can
remain short; an instruction approving a change needs the evidence conditions
for that decision. Indexed callers are not a census of every possible caller,
and a suggested test list is not executed coverage. Remove unsupported savings
or completeness claims without making correct instructions timid or unusable.

For evidence-consuming guidance, state which observations support the requested
decision. Follow the verification guide's command-specific distinctions between
completed zeros, violations, partial computation and delivery-only elision;
preserve useful findings without calling them complete clearance. Required scope,
freshness and detail still matter. Resolve conflicting prose against the actual
contract and consumer requirements; do not silently choose the easier rule.

Keep imperative tool text and executable follow-ups aligned with the actual
contract. Consult AGENTS.md for current facts/verdict conventions; do not copy
its changing vocabulary lists. Counts belong to maintained generators; a
performance comparison needs qualified measurement, not intuition or old prose.
Instruction-only corrections do not require inventing a runtime defect. A
producer/consumer defect discovered here needs its own authorized repair and
evidence qualification; a wording change cannot repair that behavior.

## Check the instruction, not just the sentence

Record revised and retained surfaces with their owners and unresolved claims.
Check the command/configuration promise against actual read-only help or inventory.
When a change alters which result states a reader accepts, or a disputed reading
matters to a decision, test a fresh reader with only the proposed instructions
and representative valid, partial and failed results. Include a correct no-change
case and withhold the desired answer. Routine syntax or spelling repairs need
their relevant checks, not a new model trial. A simulated reading is not a
live-client witness or a publication gate.

Keep a correct section when no change improves its use. Reconcile affected
siblings without copying the same paragraph everywhere. Keep private trial
records under `internal/`; distinguish draft, locally checked, released and
observed client behavior. Installation or a schema-valid tool description does
not establish that an agent actually uses the instruction.
