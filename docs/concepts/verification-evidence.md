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

### Reference evidence before removal

For `refs-text`, test search execution and graph resolution independently.
Literal hits in an excluded/oversized/unsupported file or outside a symbol span
must remain observed references with unknown reachability. A graph omission
cannot improve clearance. Verify this in text, compact and full JSON, then
across MCP delivery and handle retrieval; a handle preview alone is not the
full report. Keep a supported positive and a complete, appropriately scoped
empty-search control so the repair does not simply refuse everything.

The working-tree `static_reference_evidence_v2` contract exposes per-result
resolution, unresolved-code-reference counts and partial state, retaining the
aggregate count and qualification in the protected summary. Search failure
also makes the result partial; unrelated nonmatching unsupported files do not
erase a confirmed positive. Indexed-unreachable source hits remain `REVIEW`
without pretending the completed graph observation failed. No verdict from
this command alone establishes permission to delete. See the
[agent CLI contract](../agent-cli.md#read-refs-text-as-reference-evidence-not-deletion-permission).

## Match documentation counts to their population

Explicit example qualifiers immediately before or after a count make that
count unverifiable against current repository totals. Sentence and table-cell
boundaries keep that scope from suppressing a separate current claim, including
when the next sentence begins lowercase. Common `e.g.` and `i.e.` abbreviations
do not themselves end the scope. This is local wording recognition, not a
document-wide exemption for sample or historical reports.

`roam doc-drift` compares counts only with its available measurement authority.
For languages, that authority counts distinct language values in the local file
index, not the languages a product supports. Bare language counts therefore
remain `unverifiable`; use explicit census wording such as
`13 indexed languages`, `the index has 13 languages`, or
`13 languages in the index` when that is the intended population.
An explicit count that disagrees with the index
still reports drift. Capability wording needs a separate matching authority;
the detector does not infer one from the product name or documentation path.
An unverifiable claim keeps `partial_success: true`, even if `--ci` exits zero
because no objective drift was found. This is not proof that the claim is true.

### Documentation path context

Explicit `./` and `../` path claims resolve relative to the containing Markdown
document, including simple inline Markdown link destinations. A link label is
not a second path claim. A fragment on such a link does not change the file
existence check; this command does not verify that fragment's anchor. Literal
link syntax inside inline code is not treated as an active link.

Unqualified prose/code paths retain repository-root precedence and the existing
document-relative fallback. An explicitly relative missing target stays missing
even when a different root or sibling file exists. Escapes outside the repository,
ignored targets and unavailable ignore authorities remain `unverifiable`.
This is bounded path recognition, not a complete Markdown parser: reference
links, nested destination syntax, titles and encoded/space-containing paths need
separate link validation. Neither a verified path nor zero extracted path claims
establishes complete documentation coverage.

## Bind a review to the intended change

Capture the intended Git diff and check that Git succeeded before reviewing it.
`roam critique` refuses empty or whitespace-only piped input with exit 2 and an
`EMPTY_INPUT` JSON envelope; it does not select another change. A non-empty
`--input` patch is another explicit route. Pass `--intent` when the intended
change differs from HEAD's subject, and inspect `summary.review_source`.

For an unattended working-tree review, run `roam critique --working-tree`.
This selects `git diff HEAD`, including staged and unstaged tracked changes.
A clean tree is empty input, never a request to review the previous commit.
The flag is mutually exclusive with `--input` and `--batch`. Bare interactive
use retains the documented working-tree, then last-commit convenience, with
the selected source printed. The MCP diff tool still requires actual diff text.

## Selected checks, applicable inputs, and completed checks

### Security-review compound scope

The MCP security-review recipe combines different scopes: repository taint
observations, saved vulnerability inventory, tracked changes against HEAD for
critique, and working-tree changes for adversarial architecture checks. Its
`scope` field names each one. The vulnerability step reads `roam vulns`; it does
not import a fresh external scanner report or establish that the inventory is
complete.

The legacy `symbol` parameter does not filter these checks. A non-empty request
retains the broader observations but returns `resolution:
"unsupported_symbol_scope"`, `partial_success: true`, and a verdict saying that
symbol scope was not applied. Leave the parameter empty for the supported
repository/changeset review. Read `summary.check_status` and the child envelopes:
`completed` means the child returned without an error or declared partial state,
not proof of complete security coverage. Incomplete children remain visible and
make the compound partial; operation failures remain in `failed_subcommands`.

### Audit and Dogfood child evidence

`audit` preserves failed, incomplete, and shortened child results in
`summary.child_evidence`, including in `--brief` output. A failed or explicitly
partial child makes the parent partial and names the affected check in its
verdict. Child summary counts and state remain attached to that check; missing
tests and an empty corpus are not equivalent to a successful zero-finding scan.

`detail_omitted` means a child declared `truncation_reason: "detail_mode"`
without declaring partial computation. It does not by itself mark the audit incomplete; inspect
the retained child summary and follow-up command. A valid gate-negative result
with exit 5 is not automatically a transport failure.

`dogfood` propagates partial child summaries through `incomplete_sections` and
`child_evidence`. Its process can still exit successfully while analysis is
partial: consumers must inspect `summary.partial_success`, as the repository's
Dogfood workflow does. These disclosures preserve producer limitations, not
proof of exhaustive analysis or detector accuracy.

The workflow requires `summary.partial_success` to be the JSON boolean `false`.
Missing, null, or numeric values are UNKNOWN, not completion evidence. A
root-level partial declaration or named failed/incomplete sections also blocks
the workflow even when the summary flag says `false`.

The workflow rejects a partial summary even when all subprocesses exited zero;
its diagnostic names `failed_sections` or `incomplete_sections` when supplied.
Inspect the child evidence before choosing a remedy. Output caps and files
skipped by a scanner's size limit are different from subprocess failures, but
neither becomes complete merely because the aggregate command finished. Keep
intentional exclusions and the remaining scan denominator visible rather than
removing the gate to make a run pass.

The stale-reference reader bounds the actual byte read as well as checking the
file size. A file that grows past the limit between discovery and reading stays
unreadable/incomplete; a partial prefix is not counted as a fully scanned file.
Files excluded by discovery's size cap remain outside the observed scan.

For ordinary stale-reference scans, `directories_unenumerable` counts discovery
failures at directory boundaries. `unenumerable_directories` names those paths.
Their file population is unknown: they are not added to `files_unreadable` or
`files_scanned`. Their presence makes the scan incomplete and fails `--gate`,
including when the readable files contain no stale references. JSON, text and
SARIF carry this limitation; a directory suffix cannot establish its contents.

Anchor lookup is a separate observation. `anchor_targets_unreadable` and
`unreadable_anchor_targets` identify failed anchor reads even when source text
was readable. The anchor reader also bounds the actual byte read; it does not
extract a supposedly complete anchor set from an oversized prefix.

An explicit `--ignore-target` exclusion is applied before anchor-definition
reads, including in-page fragments. A whole-target exclusion also skips its
post-resolution existence probe; a fragment-only exclusion does not waive
file existence or checks of other fragments. These are scope decisions, not
successful validations. Target exclusion does not exclude that file as a source:
if its source text cannot be read, the scan still reports incomplete coverage.
Incomplete ordinary scans put evidence recovery first in `next_steps`, even
when findings are present. They may offer fix preview for inspection, but do
not recommend applying rewrites before the coverage gaps are resolved. This
guidance is not a change to the explicit ungated-apply command's authority.

Reference-target existence is checked against the filesystem, not inferred from
the discovery list. A target removed after discovery is missing; an I/O failure
while inspecting it is unknown. `targets_unstatable` and `unstatable_targets`
preserve that separate observation in the JSON, fix and attestation paths; SARIF
also carries the incomplete-state notification. Target observations are cached
only within one scan, not across scans; this is not an atomic filesystem snapshot.

HTML anchors are parsed as attributes: IDs can belong to any element, including
`main` and custom elements, while legacy named links require an `a` element.
Comments, script/style text and fenced Markdown examples do not supply HTML
anchors. Quoted delimiters, unquoted values and character references are parsed
without requiring a particular tag layout. This is static extraction, not browser
execution or a guarantee about dynamically rendered IDs; the existing
case-normalized matching remains unchanged. Parser refusal is unavailable anchor
evidence, not an empty anchor set.

Fix preview/apply summaries and stale-reference attestations preserve the scan
counts and incomplete state. `--fix apply --gate` refuses an incomplete scan
before rewriting. The gate evaluates the pre-fix findings; successful writes
do not replace a post-fix scan. Locked files or refused rewrites make apply
results partial. Without `--gate`, partial fixes can still be reported, but
they are not a complete verification result.

Watch mode reports scan gaps and later coverage recovery independently of
finding changes. Recovery means the latest scan reported no unreadable inputs,
not proof of exhaustive discovery. It is informational: `--watch --gate` is
rejected; use `--gate` in a separate one-shot scan. Watch rejects `--attest`, `--baseline-save`,
`--github-summary` and `--diff` rather than silently ignoring them. Fix mode
similarly rejects `--attest`, `--github-summary` and `--sarif`; generate those
artifacts with a separate ordinary scan.

MCP handle cleanup is separate from analysis and response storage. If its
directory probe or cleanup orchestration fails after the response is stored,
the handle remains available, but the outer summary is partial and
`maintenance.state` is `unknown` with reason `handle_gc_unavailable`. The preview
retains the producer's analysis state. Individual eviction races are still
best-effort; absence of this warning does not certify exhaustive cleanup.

Audit's `debt_total` is an estimate in remediation minutes; `debt_score_total`
is the separate sum of hotspot-weighted file scores, not time. Their definition
sidecars name those units. `dead_count` sums the dead-export SAFE, REVIEW and
INTENTIONAL action buckets; it excludes unused assignments and dataflow findings
and does not establish that every candidate is safe to delete. A missing bucket
or absent time estimate remains null rather than a fabricated zero. Valid zeros
remain zero.

Both aggregators parse child stdout separately from stderr diagnostics. A
structured result from a process that exits outside 0/5/6 retains its observations
alongside a failure marker and exit code. Receiving valid JSON does not erase a
process failure, and a diagnostic on stderr alone does not invalidate valid JSON.
Exit 6 retains its observations as partial analysis, not a transport failure.
Missing, empty, or malformed child summaries cannot establish completeness;
valid sibling sections remain available. Unavailable Audit counts are null,
including file, symbol, test, API, danger-zone and stale-reference counts.
Debt's finite `--limit` remains a partial list, even when its aggregate totals
were computed. Use `roam --json --budget 0 debt --limit 0` to collect every
computed row; Audit requests this row-unlimited form internally. Grouped JSON
reports returned and total file counts, and discloses per-directory row caps.
Grouped text names hidden files. `--limit 0` also includes all requested ROI
rows; a negative limit is a usage error. Removing the row limit does not remove
computation failures, unavailable inputs, or the separate JSON token budget.

Health's default SQL caps remain incomplete evidence: they limit which
candidates are severity-classified, not just which rows are printed. Use
`roam --json --budget 0 --detail health --all-issues` for all qualifying indexed
candidates, or `all_issues: true` on `roam_health` over MCP. Audit requests this
classification scope internally; this does not remove failed queries or unknown
inputs. Without `--detail`, the formatter can intentionally omit the classified
lists while retaining their counts.

`summary.issue_collection_scope` distinguishes `top_candidates` from
`all_qualifying_indexed_symbols`. The 0–100 score retains the historical
top-candidate calculation, named by `health_score_scope: legacy_top_candidates`;
full classification is not a new score definition or a score migration.
Audit/Dogfood propagate the score scope and `health_issue_collection_scope`.
`--all-issues --baseline` is refused because stored comparisons do not carry
this full-classification scope. Default baseline behavior is unchanged.
`--persist --all-issues` intentionally emits the full classified finding set
to the local findings registry; default `--persist` emits the bounded collection.
The registry upserts findings and can retain older records; it is not a fresh
snapshot census. Choose the collection scope before comparing persisted runs.

Health SARIF includes collection and analysis failure notifications in the
uploaded document as well as stderr. An empty result list accompanied by these
notifications is incomplete analysis, not proof that no issues exist.

MCP Health's compact presentation names `truncation_reason: detail_mode` and
provides a complete-classification follow-up command. Stored response handles
carry the originating command metadata so Health's output validation can accept
the handle. A valid handle still requires fetching before consuming its analysis.

Both aggregators pass the caller's token budget to their children: `--budget 0`
removes token-budget truncation at those boundaries, not scan limits or failures.
An omitted budget remains omitted in child invocations, preserving the configured
default cap rather than silently turning it into explicit unlimited output.
The repository Dogfood workflow requests that uncapped artifact explicitly and
still rejects incomplete analysis. Brief Audit output does not advertise
available detail merely because a failed child returned transport metadata.

`metrics-push` forwards an explicit global budget to its internal Audit. An
omitted budget retains the configured default cap, so larger reports may be
refused even when their source computations completed. Inspect recovery with
`roam --json --budget 0 metrics-push --dry-run` before deciding to upload: this
removes delivery caps, not source gaps or failed checks. A positive explicit
budget is also forwarded. Uncapped collection can use more memory; it is a
deliberate caller choice, not the default or an automatic retry.
Keep that same global budget on an approved live push after removing
`--dry-run`; the dry-run's budget is not saved for the next invocation. Supply
only an endpoint and credentials you are authorized to use. Budget recovery
does not establish that a receiving service is available or ready.

`metrics-push` refuses to POST when Audit evidence is partial, truncated or
missing required metric sections/counters. `--dry-run` remains available to
inspect the failed audit status and reason; its metrics object is empty rather
than populated with zero defaults. Actual measured zeros survive metric
projection. This producer-side refusal does not certify the receiving service.

Payload assembly has its own gate: a failed builder or empty/non-object metrics
payload is refused before HTTP dispatch, even if Audit completed. The summary's
`payload_status` distinguishes `ready`, `failed` assembly and `withheld` because
Audit evidence was incomplete. A successful Audit alone is not permission to
send a fallback payload. The local readiness check is not receiving-API schema
certification.

Audit and Dogfood forward an explicit `--budget` to the aggregate formatter.
When it drops detail, `truncation_reason: "budget"` and `partial_success: true`
describe the shortened response; child summary evidence remains available.
Audit's `--brief` instead declares intentional elision with
`truncation_reason: "detail_mode"` and a `detail_command`. Brief mode preserves
the computation's existing partial state rather than inventing a failure.
The formatter protects summary and contract fields: a requested budget is not
a hard byte ceiling when those protected fields alone exceed it. Do not equate
short output with complete computation, or preserved summaries with full detail.

For large MCP responses, the handle preview retains a small summary unchanged.
If the preview exceeds 4 KiB of serialized JSON, it switches to bounded
orientation fields and declares `preview.detail_omitted: true`. The full stored
payload is unchanged; use `roam_fetch_handle` to retrieve its evidence. Boolean
`partial_success` is mirrored into the handle's top-level summary when present,
so storing an incomplete result does not make it appear complete. This bounds
the preview, not every field of the enclosing handle or every CLI summary.

If a large MCP result cannot be saved, the response declares `isError: true`,
`status: "partial_failure"`, and `summary.state:
"response_storage_unavailable"`. It returns a bounded preview, not an unusable
handle or the original oversized payload. `detail_available: false` describes
delivery: the underlying operation may already have completed. Restore storage
and check the operation's outcome before retrying, particularly for tools with
side effects. Automatic retry is not recommended. Repeated-error compaction
retains this recovery guidance and the delivery-failure state.

Audit and Dogfood treat an explicitly budget-truncated child as partial even
when its own partial flag contradicts that disclosure. Intentional
`detail_mode` elision alone remains a presentation choice. Missing or unfamiliar
truncation reasons remain incomplete, including top-level truncation flags;
an explicit clean flag does not cancel that uncertainty. Audit rejects
non-object child summaries, including empty arrays, strings, and booleans;
missing or null summaries retain the existing absent-data behavior.

### Verify input eligibility

Selecting a check does not establish that it inspected anything. `verify`
records input eligibility under each selected category's `applicability`:
`applicable`, `not_applicable`, or `not_assessed`. The assessed set is the default
checks plus complexity, command examples, and claims. Other optional checks
retain their invocation and are explicitly not assessed by this input filter.
Eligibility is not execution coverage or proof of detector correctness.

`summary.checks_run` lists invoked checks, including ones that subsequently
failed. Read their availability/execution fields and `verification_complete`
before treating the invocation as a completed check. Known inapplicable checks
are not invoked or credited in the composite. Their numeric category scores
retain the legacy not-run default for consumers that require integers; the
applicability record names that default. It is not measured quality.

If changed files have no applicable selected checks, the gating command exits 5
with `state: "no_applicable_checks"`, `verification_complete: false`, and
`partial_success: true`. A clean tree remains the separate `no_changes` case.
Missing targets, unavailable checks, and incomplete execution keep their own
failure reasons. Report mode remains non-gating, with the same disclosures.

Docs-only changes can still receive useful checks. The default secret scan can
read Markdown and YAML; `--checks command_examples` can inspect documentation.
Such results identify non-code-only scope in text and agent facts. The top-level
`check_applicability` record gives the source-path denominator and inapplicable
check names. JSON retains the canonical PASS/WARN/FAIL enum and existing scope
shape for consumers: read those qualifications rather than inferring source
verification from PASS or score 100 alone. A syntax-only request over Markdown
or YAML has no applicable inputs and cannot pass.

A tracked deletion still belongs in the request receipt. If syntax is the only
selected check and no source remains to parse, a matching receipt does not turn
that request into completed verification: it returns `no_applicable_checks`.

## Running the right project's tests

Before executing a selected test list, check that the change itself was
measured. `roam test-impact` returns `diff_unavailable` and exit 6 when its Git
diff fails, with the reason preserved in JSON/MCP and an unsuccessful SARIF
invocation. An empty test list in that state must not be used to skip testing.
Successful selection still establishes only the graph mapping described in
[detector evidence](detector-evidence.md), not execution.

`roam verify --checks tests` runs the impacted Python test files, not the whole
test suite. It looks for the nearest `.venv` or `venv` inside the repository,
starting beside each test file, and runs pytest from that environment's project
directory. With no project environment, it uses the interpreter running Roam
and the repository root. It does not install pytest or dependencies for you.

Every impacted file must select the same environment, including files beyond
the execution cap. A batch spanning different environments stops before any
tests run; run each project separately. An outside-repository path, nested Git
repository, broken environment, launch failure, missing pytest, interrupted
collection, or zero collected tests cannot count as passing verification.
Timeouts and capped execution remain incomplete. An ordinary assertion failure
is a failed test run, not a missing environment.

A zero pytest exit code is not enough. Roam requests a fresh temporary JUnit
report and checks its testcase outcomes against the reported counters.
Collect-only, setup-only, entirely skipped/expected-failure runs, missing or
inconsistent reports, and reports over 16 MiB cannot establish passing test
execution. Successful verification requires at least one reported passing test
and no failures or errors. A mixed passing/skipped run exposes both counts;
it does not mean the skipped tests executed. The report is removed afterward.
Project selection settings remain in effect: these results do not establish
that every test in each targeted file was selected or that coverage is complete.

Pytest's combined stdout/stderr capture is limited to 8 MiB. Overflow, capture
failure, timeout, or unverified process cleanup keeps the selected check
incomplete even if pytest already wrote a passing report. The
`test_execution.process` receipt records the capture state, output budget,
timeout, and `tree_terminated` result. Windows uses a job object; Linux uses
a private subreaper and process-identity handles. Their cleanup includes test
descendants, including inherited output writers, after root exit or timeout.
Linux prefers Python's pidfd wrappers and can use the same kernel handle APIs
through libc when a Python build omits those wrappers. If neither backend is
available, or the kernel refuses the handle, execution stays fail-closed; this
does not substitute numeric-PID signalling for an identity-bound handle.
Cancellation also tears down the owned process boundary. The execution deadline
has additional bounded cleanup time; it is not a hard wall-clock return promise.

On other POSIX platforms only process-group cleanup is available; an unverified
descendant tree is reported as incomplete, not silently accepted as a passing
gate. Linux without the required containment primitives and Windows without a
usable job boundary refuse test launch. These bounds are lifecycle controls,
not a sandbox: tests can still write files, access the network, and exercise
your account's authority while running. Tests that intentionally leave a
background service need an explicit lifecycle outside this bounded check.

Static discovery may report `no_impacted_tests` when it finds no mapped tests;
that does not mean a test ran. In proof mode, missing executable evidence is
an incomplete check.

The test result records `test_environment` (interpreter, working directory, and
selection source), alongside execution and completeness fields. `test_execution`
names report availability and, when valid, testcase/pass/failure/error/skip
counts. Expected failures are included in the JUnit skipped count. These are
reports from the selected process, not authenticated proof against a test
plugin or project that deliberately forges its own results. Project tests,
pytest plugins, and configuration execute with your user account's authority;
choosing a virtual environment is not sandboxing. Use a trusted checkout and
retain the project's normal full-suite and CI checks.

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

Same-family review is a coverage qualification, not an exemption from receipt
validation. Artifact identity, review errors, rejection and blocking findings
are checked before the `same_family` warning can be returned. A valid accepted
same-family review retains that warning; stale or rejected evidence still blocks.

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
