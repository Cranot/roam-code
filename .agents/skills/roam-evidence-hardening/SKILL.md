---
name: roam-evidence-hardening
description: "Investigate and harden Roam detectors, CLI/MCP result contracts, and evidence consumers when dogfooding or correcting incomplete, misleading, or inconsistent analysis. Use for a requested hardening pass, a suspected evidence defect, or unresolved Roam result/delivery state, not routine feature edits, product copy, or release approval."
---

# Roam evidence hardening

Turn one concrete uncertainty into a reproduced defect, a supported correction,
or a justified no-change decision. More findings and more refusals are not the
objective. The consumer must distinguish a measured result from an unavailable
measurement without losing useful observations.

## Establish the question and boundary

Resolve the checkout and read its current `AGENTS.md` and
`docs/concepts/verification-evidence.md`. For detector claims also read
`docs/concepts/detector-evidence.md`; for environment/index trouble use
`docs/repository-maintenance.md`. These are maintained authorities, not rules
to copy permanently into this skill. Read the relevant implementation when a
source/doc disagreement affects the decision.

Separate a request to diagnose from authorization to change code. In a broad
hardening request, choose a bounded seam with a plausible consumer consequence;
name what observation would disprove the concern. Follow the actual producer,
serialization/dispatch, wrapper and acting consumer. Do not stop at a helper
whose output never reaches that consumer.

Keep the correction's job explicit. A prose-only instruction error needs checking
against the real contract, not an invented runtime failure. A relevant red/green
regression qualifies a repair, not a comparative speed or workflow-value claim;
qualify that comparison separately without holding a concrete repair for a study.

Capture the selected files/diff, including relevant untracked content, before
analysis. Check the diff producer succeeded; empty input is not permission to
review a different commit. Use the checkout's `.venv/Scripts/roam.exe` on Windows
or its verified venv equivalent (such as `uv run --no-sync roam`), not bare PATH
`roam`. Preserve an explicitly requested launcher. Inspect current help for
flag placement and command names. Use full JSON for evidence acted on; save
exact argv, cwd, stdout, stderr, exit, revision/diff identity and index state
under ignored `internal/`. A help call is not an analysis run.

## Interpret before repairing

Check execution, input scope, computation completeness and delivery separately.
Exit 0, an empty findings list or a valid schema alone proves none of these.
Absent, malformed or contradictory evidence stays unknown; preserve legitimate
zeros and valid findings from completed portions. A zero-scan result cannot
establish absence in the requested population. Read command-specific status,
denominators, metric definitions and bounds, not invented universal fields.

Distinguish intentional `detail_mode` elision from token-budget truncation,
computation caps and unavailable inputs. A completed, current zero-finding scan
can be valid within its stated bounds. Do not call intentional presentation
elision a failed computation. Conversely, removing `--budget` limits does not
repair a failed scanner or broaden the indexed population. Fetch an MCP handle
before consuming its stored analysis; a preview is not the full artifact.

A valid gate-negative result is different from transport failure. Stderr may
contain diagnostics alongside valid stdout JSON. A response-storage failure may
occur after the operation took effect: inspect its outcome and recover delivery
before deciding whether a retry is safe, especially for mutating tools.

For a detector finding, inspect the matched source and its assumptions: receiver
identity, loop placement, mutation/order, language/framework and graph resolution.
A method name or heuristic score is a lead, not a demonstrated defect. Preserve
metric meaning when comparing commands. A source hash proves identity, not the
truth or completeness of the reported result.

## Qualify the smallest correction

Freeze a defect-specific test and observe its relevant failure on the unfixed
implementation in an isolated setup. Run the same test on the repair with valid
controls; for a false-positive fix retain a genuine positive. If expected
behavior changes, repeat both sides. Import errors or empty collection do not
establish the regression. Then exercise the real CLI or serialized boundary
and the consumer decision, including incomplete and valid inputs.

Keep tests independent of accidental clock, network, shared index and module-
cache state. Real filesystem/subprocess integration can be necessary: use a
controlled fixture and bounded lifecycle, and explain what it verifies. Do not
replace the very boundary under test with a mock. Measure proposed optimizations
against a simpler baseline while preserving semantics and evidence limits.

Run affected tests and the project's required gates in proportion to the actual
diff. A static test mapping is not execution; skipped tests remain skipped.
Use bounded workers and isolate index writers. Do not execute write/network
commands merely to claim complete dogfood coverage; record the authority gap.
Within the selected dogfood scope, run safe read-only checks rather than judging
them inapplicable without trying them. Verify prerequisites and side effects;
a completed zero is an observation, while zero scanned cannot establish absence.

Finish with the defect/no-change decision, exact evidence, repair scope, surviving
uncertainties and next useful action. Update handwritten behavior docs or their
owning generators as appropriate; regenerate generated output rather than
hand-editing it. Preserve historical records, and keep new dated operational
measurements private. Separate local repair qualification from whole-release
review and publication.
