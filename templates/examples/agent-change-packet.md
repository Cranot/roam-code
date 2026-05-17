# Agent Change Packet

Use this packet when an AI coding agent is about to edit a Roam-indexed
repository. Paste it into an issue, PR description, or agent handoff. The
goal is simple: the agent reads the right context before editing, declares the
risk it is accepting, and leaves enough evidence for a reviewer to trust the
change.

## Change Request

| Field | Value |
|---|---|
| Request owner | `[NAME / TEAM]` |
| Repository | `[ORG/REPO]` |
| Branch / range | `[BASE]..[HEAD]` |
| Target symbol or path | `[SYMBOL_OR_PATH]` |
| Desired outcome | `[ONE_SENTENCE_OUTCOME]` |
| Out of scope | `[WHAT_THE_AGENT_SHOULD_LEAVE_ALONE]` |
| Reviewer | `[HUMAN_REVIEWER]` |

## Agent Contract

The agent must complete these steps before asking for review.

```bash
roam runs start --agent "[AGENT_ID]"
roam mode safe_edit
roam pr-bundle init
```

If the change requires schema migration, filesystem-wide rewrites, generated
artifacts, or staged commits, switch to the narrowest higher mode that applies
and record why:

```bash
roam mode migration
# or
roam mode autonomous_pr
```

## Pre-Edit Evidence

Run these commands before editing. Paste the one-line verdict and the evidence
file path, handle, or command output summary into the table.

| Required evidence | Command | Result |
|---|---|---|
| Codebase orientation | `roam understand` | `[VERDICT / SUMMARY]` |
| Task-specific retrieval | `roam retrieve "[TASK]"` | `[TOP FILES / HANDLE]` |
| Exact context to read | `roam context [SYMBOL_OR_PATH]` | `[FILES + LINES]` |
| Blast radius gate | `roam preflight [SYMBOL_OR_PATH]` | `[VERDICT]` |
| Impact map | `roam impact [SYMBOL_OR_PATH]` | `[CALLERS / TESTS / RISK]` |
| Existing findings | `roam findings list --limit 20` | `[RELEVANT FINDINGS]` |
| Policy/rules check | `roam rules-validate .roam/rules.yml --explain` | `[PASS / WARNINGS]` |

If any command returns `partial_success: true`, unresolved paths, stale index,
or missing state, stop and record the state. Do not treat an absent signal as a
safe signal.

## Edit Plan

List the intended changes before touching files.

| File or symbol | Planned change | Why this file is in scope |
|---|---|---|
| `[PATH_OR_SYMBOL]` | `[CHANGE]` | `[EVIDENCE FROM PRE-EDIT COMMANDS]` |
| `[PATH_OR_SYMBOL]` | `[CHANGE]` | `[EVIDENCE FROM PRE-EDIT COMMANDS]` |

## Risk Gates

Mark each gate before editing.

| Gate | Pass? | Evidence |
|---|---|---|
| Blast radius understood | `[YES/NO]` | `roam preflight`, `roam impact` |
| Affected tests identified | `[YES/NO]` | `roam affected-tests [SYMBOL_OR_PATH]` or `roam test-impact` |
| Clone siblings checked | `[YES/NO]` | `roam clones --persist`, `git diff \| roam critique` |
| Security surface checked | `[YES/NO]` | `roam taint`, `roam secrets`, `roam vulns --reachable-only` as applicable |
| Architecture budget checked | `[YES/NO]` | `roam budget`, `roam fitness`, `roam dark-matter` as applicable |
| Human-only zones avoided | `[YES/NO]` | `roam safe-zones [SYMBOL_OR_PATH]` |

Any `NO` requires a reviewer note before the agent edits.

## Post-Edit Proof

Run these after editing.

```bash
roam diff
git diff | roam critique
roam affected-tests [SYMBOL_OR_PATH]
roam pr-bundle emit
roam runs verify [RUN_ID]
```

Paste the results:

| Proof | Result |
|---|---|
| Diff blast radius | `[VERDICT]` |
| Critique findings | `[HIGH / MEDIUM / LOW COUNTS]` |
| Tests required | `[TEST IDS]` |
| Tests run | `[COMMANDS + OUTCOME]` |
| PR bundle path | `[PATH]` |
| Run ledger verification | `[PASS / FAIL]` |

## Reviewer Handoff

Write this section in plain language.

- What changed:
- Why the change is safe enough to review:
- Highest remaining risk:
- Tests that would catch regression:
- Files the reviewer should read first:
- Follow-up issue, if any:

## Disclaimer

This packet is engineering evidence support. It does not certify compliance,
security, or correctness. A human reviewer remains responsible for accepting
the final change.
