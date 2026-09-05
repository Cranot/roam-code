# Verification evidence: what a green result establishes

Roam organizes evidence and applies configured checks. A valid JSON document,
a passing command, a source hash, and a correct repair establish different
things. Keep the claim no broader than its evidence.

## From a question to a useful check

Start with one concrete uncertainty in the current change. Identify the
affected behavior, the smallest observation that would distinguish a defect
from a correct implementation, and the result that would disprove the concern.
Use existing tests, command output, and evidence readers before adding another
analysis stage. A useful review can conclude that no material gap was found.

For a regression correction:

1. Freeze a small test that demonstrates the intended behavior. Run it against
   an identified implementation without the repair and inspect the failure.
2. Run that same test against the repaired implementation, alongside a valid
   case that must remain accepted. Check that failures concern the claimed
   defect, not an import error, missing dependency, or broken test selector.
3. Exercise the actual consumer: serialize and reload an artifact, invoke the
   CLI, or cross the relevant producer/consumer boundary. Then run the broader
   affected tests and appropriate repository gates.

Keep the test and its expected behavior fixed between the bad/good comparison.
If the test changes, repeat both sides. Run old revisions in an isolated
checkout; preserve the active worktree and its uncommitted changes. Do not give
generated probes authority to execute arbitrary commands or modify protected
tests, admission policy, credentials, or deployment state.

## Proof bundles and verdicts

`roam pr-bundle` records incremental preparation evidence. `roam proof-bundle`
composes the versioned `AgentChangeProofBundle`; `roam verdict` reads its inputs
and computes a verdict. Composition and schema validation do not independently
authenticate agent-supplied statements or prove that tests ran against the
current change.

For an existing preparation bundle:

```bash
roam proof-bundle --bundle .roam/pr-bundles/main.json --output .roam/proof.json --strict
roam --json verdict --bundle .roam/proof.json
```

Replace the input path with the bundle for the branch you are reviewing. The
first command writes the composed artifact even when `--strict` returns a
nonzero verdict. Inspect that artifact; do not interpret its existence as a
successful gate. These commands do not run the required tests for you.

The composer preserves the review inputs that its verdict used:

- `orchestration_contract`: declared review obligations.
- `review_evidence`: the supplied review-verifier results. `null` retains the
  legacy no-review-input path; `{}` is an explicit review opt-in with no results.
- `change_set_unanalyzable`: an explicit reason when Git cannot fully enumerate
  the fallback change set, including untracked files. Already discovered paths
  remain visible, but do not make the scan complete.

A rejected or stale review must not become a passing verdict merely because
the bundle was written to disk and read again. The same applies to missing
required reviews and review-coverage warnings. This is a transport invariant,
not a new review policy or a new source of authorization. Stripping fields from
an unsigned, agent-authored bundle remains outside this guarantee; independent
CI or another authority must enforce stronger trust requirements.

When a bundle declares paths, composition uses those paths. It does not prove
the declaration is exhaustive or bind each test result to the current tree.
When no paths are declared, the Git fallback checks tracked and untracked
changes. A failed scan is unknown, not a measured empty set. Git filenames are
read as NUL-delimited records so Unicode, whitespace, and embedded newlines do
not change the identity of a path.

## Evidence levels and their limits

| Observation | Establishes | Does not establish |
| --- | --- | --- |
| Source file or function exists | A mechanism is present in inspected source | Its caller, configuration, or consumer is active |
| Source/content hash matches | Identity under the stated hashing convention | Relevance, correctness, or effectiveness |
| Schema validation passes | The checked structural constraints hold | The claim is true or the artifact belongs to the right task |
| Ledger verification passes | The checked local chain is internally consistent | Every recorded assertion is true or independently authorized |
| Required test is recorded as passing | The supplied record satisfies the applicable status check | Authentic execution, current-tree binding, or defect-specific coverage |
| Fixed bad/good regression pair distinguishes the revisions | The fixture detects the specified defect under that setup | Full production coverage or correctness on every input |
| A configured execution reaches a guard and an acting consumer | A runtime witness for that execution and configuration | Unobserved deployments or every future execution |

For a claimed enforcement path, inspect the entry point, active configuration,
mechanism, durable observation, and consumer that acts on it. A warning with no
acting consumer is visibility, not enforcement. A proposed source-level path
can guide a runtime check; it cannot substitute for that check.

## Benchmark accounting

The live `roam bench-compile` command reports assigned `cells`,
`dispatched_cells`, `reused_cells`, and `parsed_cells`. A dispatched cell is a
harness attempt, not provider-confirmed delivery. Cached baseline reuse is not
a new dispatch or an independent sample. An unsuccessful result envelope,
missing artifact, or failed dispatch cannot count as parsed success. An older
file in a reused output directory cannot rescue a failed current dispatch.

Per-condition metric aggregates are conditional on parsed successful result
envelopes, with each metric's own `n`. Missing measurements remain unknown.
`partial_success` discloses missing results or failure to persist `cells.tsv`;
`cell_records_persisted` reports the latter explicitly. The TSV retains its
original four columns. Save the command's JSON envelope alongside the raw
results if you need assignment and dispatch accounting later.

For saved raw cell files, use the read-only analyzer:

```bash
python scripts/bench_analyze.py internal/benchmarks/my-run --timeout-cap 180
python scripts/bench_analyze.py internal/benchmarks/my-run --timeout-cap 180 --json
```

Supply the timeout cap actually used by the run. The analyzer's default is a
historical 90 seconds, not an automatically discovered run setting. The script
is repository tooling, not part of the installed CLI command surface.

The analyzer retains malformed and unreadable cells in the observed-artifact
denominator. It reports all discovered conditions, including `static` and
custom checklist conditions. Successful-result rates concern envelopes, not
verified task outcomes. Its all-observed metric view includes any valid
measurements from error results; its successful-result view is explicitly
conditional. Both name observed and unknown measurement counts. Recorded zero
is valid; missing, negative, non-finite, boolean, or text-valued metrics are not
free work. Timeout wall estimates are separate, and missing cost is not imputed.

Saved cell filenames alone do not establish assignments, dispatches, missing
files, cached reuse, retry lineage, served model/effort, or protected-oracle
outcomes. The analyzer leaves those properties unknown. A directory with no
cell artifacts, an invalid directory, or a non-positive timeout cap returns
exit 2. Exit 0 means an accounting report was produced, not a successful
benchmark or evidence that one condition improved quality.

## Comparing analytical approaches

Evaluate an added analysis step against the incumbent and a short, task-specific
checklist. Hold task, input evidence, model/harness, instruction carrier,
available tools, output contract, and resource budget constant. Qualify carrier
changes separately. Record requested and provider-observed settings separately;
an alias or a generated self-description is not a served-model observation.

Measure independently verified useful findings and missed defects, together
with false allegations, false refusals, malformed outputs, timeouts, and
infrastructure failures. Include complete cases that should pass. More prose,
more generated tests, stylistic novelty, or more refusals is not itself better
verification. Preserve the full assignment denominator where the harness
records it, and distinguish it from the successfully delivered/parsed subset.

Split development and confirmation cases by independent incident or project,
not near-duplicate outputs or retries. Keep protected labels outside the
optimizer's input. A confirmation set used to revise an approach becomes
development data. Specify scope, budget, critical regressions, and retirement
criteria before comparing results. Remove an optional stage if the simpler
baseline does as well; do not weaken mandatory checks to improve a score.

Historical prompt-design observations can motivate interface conventions.
They are not universal laws across current models, carriers, or tasks, and
they do not by themselves qualify an automatic routing or promotion policy.

## Maintaining documentation claims

The saved-file reader, `roam verdict`, rejects ambiguous or malformed input:
duplicate JSON keys, non-finite numbers, and wrong-shaped evidence fields.
These return a structured `bundle_parse_error` with exit 2; unreadable files
return `bundle_load_failed`. An unknown review status returns
`unmapped_review_status`, also at exit 2. Files are decoded as UTF-8. Valid
legacy bundles remain readable, including absent or null optional review
fields; this compatibility does not authenticate their claims. A parser
refusal is not a verdict and must not be treated as approval.

The same input checks apply to `roam proof-bundle`, `roam guard-pr`, and the
composer's consumed evidence fields. `guard-pr` validates the original file
before auto-collection, rechecks it at collection time, and validates collected
fields before saving. Invalid input is not rewritten into an apparently valid
bundle. Failed collection returns `auto_collect_failed` at exit 2, even without
`--strict`; use `--skip-collect` only when deliberately reviewing saved evidence.
Expected composition failures, including unknown review statuses in the producer
commands, return `compose_failed` at exit 5 without emitting or logging a verdict.
These are input/operation failures, separate from the optional verdict gate.

Use deterministic checks first for removed paths, stale hashes, unsupported
IDs, and broken links. Then ask whether the cited evidence demonstrates the
specific claim under the stated source and configuration. Valid but irrelevant
citations are still evidence gaps. Preserve historical results and explicitly
revise affected current claims; changing a reference does not renew efficacy.

See [detector evidence](detector-evidence.md) for static-analysis limits,
[repository maintenance](../repository-maintenance.md) for gates and handoff
discipline, and [the documentation map](../README.md) for maintained sources.
