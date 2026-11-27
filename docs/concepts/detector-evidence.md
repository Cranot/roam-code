# Interpreting detector evidence

Search-backed deletion checks also depend on a complete search: `grep` preserves
partial matches and reports engine failures, `refs-text` downgrades unconfirmed
absence to `REVIEW`, and `delete-check --ci` refuses incomplete searches in every
output channel. See the [agent CLI guide](../agent-cli.md).

The working-tree `refs-text` contract also separates text evidence from graph
membership. A source hit without an enclosing indexed symbol has
`reachable: null`, not `false`, and makes its result partial. Known positives
remain useful alongside unresolved hits. Even a hit not reached in the indexed
graph requires review: no indexed caller does not rule out runtime use. The
legacy `SAFE-TO-REMOVE` label is limited to no source-code hits in the selected
search scope, not a deletion authorization. These meanings are identified by
`summary.verdict_definition`; consult that field on the installed version.

Roam is a local static-analysis tool, not a compiler, runtime coverage collector,
or proof that a change is safe. Start with the verdict, then check the scope,
resolution, evidence, and incomplete-result fields before acting on a finding.
An exit code of zero can accompany useful findings, a disclosed prerequisite
gap, or partial analysis. It does not mean every check passed.

## Match the claim to the measurement

| Command family | What the result measures | What it does not establish |
| --- | --- | --- |
| `orphan-imports`, `verify-imports` | Indexed/local source paths and the supported dependency model | Successful compilation, package export-map validity, or complete bundler resolution |
| `dead`, `safe-delete`, `uses` | References visible in the indexed graph, plus command-specific public-surface heuristics | Absence of dynamic imports, reflective calls, external consumers, or unresolved member access |
| `algo` | Structural/source patterns and their stated assumptions | That every proposed optimization preserves semantics or improves measured performance |
| `effects`, `side-effects`, `tx-boundaries` | Recognized effect and transaction evidence | Purity, complete framework coverage, or a runtime transaction trace |
| `test-map`, `ai-readiness` | Static test naming and dependency relationships | Runtime line/branch coverage or a passing test suite |
| `cycles`, `cycle-break` | Strongly connected components of their respective symbol/file graphs | That a component's sorted members form one traversal, or that all components are actionable |
| `ai-ratio` | An uncalibrated score of source/Git patterns | The percentage of code written by AI or the authorship of an individual file |
| `health`, `vibe-check`, `pr-risk` | Heuristic composite scores with documented inputs | Build correctness, test success, release approval, or permission to modify code |
| `path-coverage` | Representative graph paths with static test mappings, within the reported traversal bounds | Exhaustive path enumeration or runtime execution/coverage |
| `taint` | Reported source/sink paths or explicitly labeled co-occurrence, under the selected rules | Exploitability, complete rule applicability, or a clean security assessment |

Inspect `partial_success`, `resolution`, `state`, `warnings_out`, truncation,
and metric-definition fields where present. A low-confidence result is a reason
to inspect evidence, not a reason to discard it or to promote it into certainty.

For taint findings, inspect the source, the specific sink argument reached,
sanitizers, and execution context. An environment value passed as a subprocess
environment is not by itself proof of shell-command injection. Co-occurrence is
weaker than computed dataflow, and even a computed path requires security review.
Inspect rules with zero anchors and budget-truncated results before interpreting
an empty or short finding list. The Python text-anchor pass defers argument-flow
analysis until a same-function source/sink pair can consume it, then reuses that
file/rule's result for its remaining pairs. Source, sink and sanitizer anchors
remain available to the separate graph-reach passes. This avoids unused work;
it does not expand the detector's dataflow or language coverage.

For `path-coverage`, depth pruning and shared
visited-node traversal prevent an exhaustive-path claim even when no test gaps
are reported. Its test signal is static, not measured runtime coverage.

## Clone scans and patch review

`roam clones --persist` saves the detected pairs and a scan record together.
An interrupted detector or failed save preserves the previous saved snapshot.
A successful scan with no pairs is different from never having scanned.
Scanning reads its file list and index identity from one database snapshot;
review likewise reads saved metadata and pairs together, even if another
process refreshes them during the command.

Inspect `summary.scan`: it names the scope, filters, similarity threshold,
minimum function length, eligible and compared function counts, unavailable
file count, and completion state. The detector currently compares at most
2,000 qualifying functions, favoring larger functions when capped. If comparison
fails before finishing, the compared-function count remains unknown (`null`),
and the scan is incomplete. This limit is separate from the display limit.
A cap or unavailable parser/source marks the
scan incomplete; an empty result then cannot establish absence of clones.

`roam critique` reads this saved evidence, including for batch reviews. Its
`clone_scan` summary retains the detector's bounds. A filtered, incomplete,
older-format, changed-detector, or stale scan produces a qualified check status
and `partial_success: true`. Positive findings remain visible for inspection.
Old databases migrate without inventing completion records for their existing
pairs. A completed, current empty scan can count as a check that ran.
The completion bit alone is insufficient: counts and bounds must agree, and
the saved source inventory must match the indexed eligible files. Contradictory
or missing metadata qualifies the review without hiding useful positive findings.
`NaN` thresholds are rejected before analysis or persistence; JSON callers
receive a structured usage error with exit code 2. Malformed
saved numbers cannot leak `NaN` or `Infinity` into the review's JSON summary.

`roam oracle is-clone-of SYMBOL` uses the same saved-scan qualification. With
no matching pair, missing, capped, filtered, inconsistent, or stale evidence
produces `value: null`, `verdict: "indeterminate"`, and `partial_success: true`.
A current completed scan permits a negative answer only within its recorded
detector bounds, exposed as `summary.clone_scan`; it is not proof that the
symbol has no semantic duplicates. Existing positive rows remain visible,
with a qualified reason when their evidence is incomplete or stale.
`summary.check_status` names that state, and batch queries preserve partial
rows and mark the batch partial. Names match literally and case-sensitively,
including the detector's `file.py:function` form and legacy dotted names.
The MCP single and batch tools preserve the same scan-evidence fields; their
tool versions are 1.1.0 so clients can refresh older cached descriptions.
Batch freshness checks use the explicitly requested project root, not the
server's working directory, and do not auto-index that unrelated directory.

`roam retrieve` qualifies its optional clone-ranking signal with the same
saved-scan checks. `clone_evidence` carries the status and scan bounds through
the pipeline, CLI JSON, and MCP. Missing, incomplete, filtered, stale, or invalid
evidence sets `partial_success: true` in the retrieval summary and qualifies the
verdict. Other retrieval signals remain useful; partial clone evidence does not
make their observed results disappear.

Previously observed matches retain `clone_cluster` and `clone_siblings`, plus
`clone_check_status` and `clone_boost_applied`. Only a complete, current scan may
boost ranking. A stale observation is a reading lead, not current clone proof;
absence of a tag is not proof of no duplicates. A completed empty scan remains
distinct from a missing scan. Candidates and saved evidence share one SQLite
snapshot, and freshness is checked once per query, not once per candidate.
Setting the retrieval epsilon weight to zero disables clone inspection and its
boost; the result records `skipped:disabled` rather than claiming a scan ran.

Freshness compares the indexed file set/generation and the eligible files'
source hashes. Refresh the index and scan after source changes. This checks
the indexed corpus under the saved detector settings, not unindexed files,
all languages, semantic equivalence, or the correctness of a proposed repair.
Source hashing and launcher metadata are provenance checks, not authentication
against someone who can rewrite the local evidence store.
Source hashing streams file contents in fixed-size chunks to bound allocation;
it does not cache freshness between invocations. Non-regular sources are
unavailable, including a POSIX file replaced by a FIFO during the type check.
This is not a bound on network-filesystem latency or an atomic filesystem snapshot.

Clone analysis avoids duplicate candidate bookkeeping and resolves saved cluster
membership only for pairs that were actually found. Fix-hint lookup reuses
function locations within the already-parsed tree for that scan; it does not
cache source freshness across commands. These are implementation optimizations,
not broader detector coverage: pair ordering, similarity thresholds, scan caps,
and incomplete-result qualifications remain unchanged. Dense clone output can
still require quadratic space in the number of participating functions.

## Python reference and test-selection precision

Bare values passed to calls or stored in registries can be genuine callback
references. They can also be ordinary local data: `project = {}; inspect(project)`
does not refer to an unrelated function or pytest fixture named `project`.
The Python extractor guards parameters and ordinary assignment bindings in
the enclosing function, including annotated, destructured, chained, and
augmented assignments. It respects `global`/`nonlocal` declarations and treats
default expressions as part of the enclosing scope. Callback assignments such
as `alias = callback` retain the reference to the actual callback; subsequent
uses of the local alias do not invent global-name edges. Function binding
scans are cached only during that file's extraction.

This is a narrow bare-value reference guard, not complete Python name resolution
or alias/dataflow analysis. Loop targets, context-manager and exception bindings,
comprehensions, assignment expressions, imports, and dynamic dispatch have
additional scope rules outside this guard. Direct-call resolution is unchanged.
Static impacted-test selection still needs corroboration from real tests.
Rebuild an existing index with `roam index --force` to refresh previously saved
reference edges after an extractor change.
This changes graph-derived metrics even on unchanged source. Snapshot metrics
definition 3 distinguishes these results from definition 2; baseline consumers
must report the version mismatch, not interpret it as an improvement or regression.

## TypeScript and browser projects

JavaScript/TypeScript member calls require receiver ownership evidence: a
class instance or static context for `this`, a direct TypeScript annotation,
a `const` construction/object, or an imported object/namespace in an indexed
relative module. An object-literal method is eligible only through its owner;
a bare call cannot select it by method name. An object method nested in a
class stops the class's `this` evidence. Static `this` selects static members,
and instance `this` selects instance members. Class-name receivers select
static members; construction and declared-type receivers select instance members. Arrow functions in class fields
retain the field's static/instance context and source symbol; crossing a nested
class without such context leaves `this` unknown.

Calls such as `this.users.findAll()` can use the declared class/interface type
of a field or constructor parameter property, including access modifiers and
`readonly`, or a field initialized directly with `new X()` in its declaration.
A qualified type such as `NS.Foo` is supported when its lexical namespace or
relative namespace import identifies exactly one class/interface. Qualified
type aliases, unions with multiple concrete owners, external namespaces,
and inferred factories remain unresolved. A same-named object value does not establish a declared type alias
as a class/interface owner. A union of one named type with only `null`/`undefined` retains that
owner; multiple concrete types remain unresolved. Named default exports and
TypeScript `export =` assignments preserve the exported identity even under a
different local import name. Namespace imports follow explicit relative
`export *` barrels, excluding default exports. Conflicting exports and
distinct same-named classes stay ambiguous. A type/value pair shares a binding
name while keeping its type and value members separate. A same-file class and
interface of the same qualified name share an instance owner, including when
the class is imported. An overloaded method resolves to the implementation
when unique, or to the first declaration in source order when there is no
implementation. Resolution never picks an owner merely because only one of
several possible owners declares the member.

Direct `Foo.bar = function () {}` and `Foo.prototype.m = function () {}`
assignments can supply static and instance edges through an evidenced function
owner. A member assigned before a unique hoisted function keeps that parent.
Receiver types and constructions use the scope of their declaration to resolve
the owner name.

Dynamic imports with a string literal specifier use the same module exports as
a static namespace import. This covers member calls on an awaited import, a
direct `const` binding to it, and the first parameter of an inline `.then`
fulfillment callback when that parameter has no writes.

Unknown receivers produce no edge. Coverage still omits factory returns,
arbitrary aliases, inherited-member lookup, destructured option callbacks,
Vue `SlotsType` inference, computed/dynamic exports, anonymous default owners,
and general CommonJS `require`/`module.exports` binding inference. Imported
bindings subsequently forwarded by a local export clause are not inferred.
External package export maps and compiler path aliases beyond the existing
`@/` convention are not resolved. The supported `export =` case is a direct
named local declaration imported with TypeScript `import X = require(...)`.
An object's own dynamic `this` remains unknown rather than being treated as an
enclosing class instance. Fields assigned only in a constructor body (without
an annotation or direct declaration initializer) remain unresolved. Local
function/object/class declarations without indexed symbols produce no local
edge; real local calls can therefore be absent. Top-level function overload
signatures without an implementation are still outside extraction.

Function parameters and local declarations, including local functions, arrows,
classes and named expressions, do not create name-based reference edges.
Unindexed local object/class owners and shadowing type parameters cannot
borrow outer symbols. Switch cases share the switch body's lexical scope.
Imported callbacks remain eligible when unshadowed. Standard name resolution
filters language families before choosing candidates. JS/TS/TSX/Vue/Svelte
share a family; C/C++ and their headers share another because both source
languages can call declarations from those headers. Other distinct registered
languages require explicit bridge resolution, even if they share a grammar.
An external bare import can still match an unrelated same-language interface
property. The owner rules do not establish complete module/type inference.

- JSX and decorators can bypass lexical and receiver evidence, so a local `Card` used in `<Card />`, a local `@decor`, and an unimported `<Layout.Header />` can bind to unrelated indexed declarations.
- Bare-call fallback can choose one of two equally plausible `helper()` targets, bind imported `format()` to a same-directory decoy, or bind `go()` to an unrelated `go` when the import is `run as go`.
- Unqualified `run()` and `make()` can falsely target the instance method `Svc.run` and the static method `Svc.make`, respectively.
- For a class `Foo`, the index treats `Foo.bar = function () {}` as an instance member and links `foo.bar()` to it after `const foo = new Foo()`.

Rebuild with `roam index --force` to remove old saved edges. Missing graph edges
remain a coverage limitation, not proof of no callers or safe deletion.

Relative runtime imports such as `./worker.js` can name TypeScript source.
The import checks share extension substitution: `.js` can resolve to `.ts`,
`.tsx`, or `.d.ts`; `.mjs` to `.mts` or `.d.mts`; `.cjs` to `.cts` or `.d.cts`.
Module lookup takes the first existing candidate in this order, with a file
such as `dup.ts` preceding `dup/index.ts` for `./dup`. These rules follow the
[TypeScript module reference](https://www.typescriptlang.org/docs/handbook/modules/reference.html#file-extension-substitution).
They do not implement all compiler options, package `exports`, project references,
or custom loader behavior. Keep `tsc --noEmit` and the project's actual build in
the validation loop.

`verify-imports` recognizes the nearest package's declared Node dependencies
separately from local imports. Explicit npm/Yarn workspace members also inherit
root `devDependencies` for shared tools; sibling runtime dependencies do not
silently become declarations for every package. Non-members and nested Git
repositories do not inherit those root tools. Malformed manifest objects or
dependency sections are not declarations, and each verification run refreshes
its manifest cache. This is declaration checking, not an installed-package or
package-manager resolution proof. `orphan-imports` treats bare package
specifiers as external. These commands answer different questions; identical
finding totals are not an invariant. A genuinely absent relative module must
still be reported after extension substitution.

Database classifications require database-specific evidence for ambiguous
JavaScript methods. A sprite's `destroy()`, canvas `save()`, application `run()`,
or array `find()` alone is not a database operation. Browser storage, DOM changes,
WebSocket construction, and selected WebGL calls have their own coarse
side-effect signals. Coverage remains heuristic: missing a recognized effect
does not prove purity. Likewise, `beginPath()` and `beginRound()` are not
transaction openers, and browser/network mutations alone do not establish that
a database transaction is missing.

## Rust method calls

A Rust method call `value.method()` becomes an edge only when the code shows the
type of `value`: `self` in an `impl` or `trait` block, a parameter or `let` with
a declared type, `T::new(..)`, `T::default()` or a struct literal, a field path
(`self.inner.method()`) through a struct declared in the same file, or a chain
through methods whose return types that file declares
(`Builder::new().a(1).build()`). The binding in effect at the call is used, so
`let v = v.unwrap_value();` reads the parameter `v`. The method must belong to
that type: its inherent `impl`, a trait `impl` for it, or the trait itself for
`self` inside a trait. A plain call `f(..)` binds only to a function or a
tuple-struct constructor, never to a method, field, enum variant or associated
type. Unknown receivers produce no edge.

Types are matched by name, and block scope is not modelled. These real calls are
still missed: calls through a generic bound or `dyn Trait` (`out.combine(..)`
with `T: Combine`), trait methods on a type from another crate
(`req.extract()` on `http::Request`), bindings from `match` and `if let`
patterns and closure parameters, a chain through a method declared in another
file, and a type whose `impl` has two methods of one name.

## Review an algorithm finding

Broad-exception findings are recovery-review candidates, not proof that an
error was silently lost. The Python detector checks each handler separately:
a raise or error record in another handler cannot excuse this one. It recognizes
direct rethrows and explicit exception-bearing return/results, including in
methods. A conditional raise, deferred nested function, or logging call alone
does not establish that every failure path is handled. Review the caller's
failure contract before narrowing a catch or adding a rethrow; additive
diagnostics can intentionally recover. Alias/dataflow semantics, tuple exception
types, and the legacy recovery-name exclusions remain heuristic limitations.
Detector version 1.1.0 changes which handlers match; changed finding totals are
not a measured improvement in the target code.

1. Open the exact reported source location and read `reason`,
   `evidence.matched_patterns`, and `evidence.context_lines` when supplied.
2. Check the assumptions: does the lookup collection vary, is a loop bounded,
   must asynchronous calls remain ordered, and does recursion revisit the same
   subproblem? A matching method name alone is insufficient.
3. Measure and run the project's tests before applying the proposed change.

The index distinguishes calls on other objects from direct self-calls and treats
loop-local assignments and known collection-mutating receivers (including `this`)
as varying inputs.
JavaScript `indexOf` / `lastIndexOf` results must be used in a recognized membership
comparison before set advice is offered; numeric positions used by `splice`,
returned, or stored are not interchangeable with `Set.has`. Searches with a
`fromIndex` argument and receivers produced by a call are not whole-collection
membership candidates. An explicit empty
modern signal is not replaced by legacy name-only matching. Known clock and
random-number reads are not hoisting candidates; other callees still require
purity and evaluation-order review. Alias mutation and arbitrary user-defined
side effects remain outside this heuristic's proof. Spread-accumulator and serial-await
checks require the matched operation inside the loop body. A power-like name and
ordinary multiplication are not sufficient for repeated-exponentiation advice.
These checks reduce false positives; they do not prove loop invariance, purity,
overlapping recursive subproblems, or safe parallelization in every program.

Refresh derived signals with `roam index --force` after installing a detector
change. Old indexed signals and newly read source can otherwise disagree.

## Graph scope and work planning

`observability-opt` reports raw-print **review candidates**, not confirmed
debug leftovers or instructions to replace a CLI's output. Python findings
require a bare-name `print` call in the parsed syntax tree. Docstrings, string
examples and comments are excluded; calls inside f-string expressions still
count. `evidence.detection_method` distinguishes `python_ast_call` from the
other languages' `line_pattern` heuristic. Neither establishes that the call
resolves to the builtin or that its output is unwanted. Other languages still
have string/block-comment limitations.

Unreadable sources and Python syntax the running interpreter cannot parse are
listed in `files_unreadable` / `files_unparsed`; JSON and MCP mark the result
partial and the verdict names the gap. Valid files can still contribute useful
findings. `source_files_scanned` counts harvested files, including unparsed ones,
not a complete-parse denominator. Profile, language and file limits define the
selected scope; a strict profile dropping heuristic findings is not a clean bill
of health. Predicate/persistence version 1.1.0 records this matching change.

The reusable resilience engine likewise marks unreadable selected sources as
partial while preserving findings from readable files. It is an engine-level
API; this does not introduce a new resilience CLI command or change its
text-pattern timeout detector into a parser-based check.

`cycles` uses the symbol import/call graph and labels cross-file actionability.
`cycle-break` uses the file dependency graph. Its `members` array is a component
inventory; `cycle_path` is a closed walk whose displayed adjacent edges actually
exist. Exact extraction recommendations have a size bound; a component without
a recommendation is not an instruction to cut arbitrary edges.

`partition` chooses a default of two to eight agents rather than allocating one
agent per disconnected component. Use `roam partition --agents 4` to set an
explicit team size. Partitions can still share files and depend on each other;
inspect conflict hotspots and merge order before assigning write access.
The `roam_partition` MCP wrapper defaults to an explicit four partitions;
it does not use the CLI's automatic count. Do not assume identical defaults
across CLI flags and MCP parameters.

For a file-level decomposition, use `roam split path/to/file.ts`.
`roam plan-refactor SymbolName` requires a symbol. `roam ask` recognizes camelCase,
CONSTANT_CASE, snake_case, and qualified member identifiers, but it is not a
general natural-language parser: review the selected recipe and resolved target.

`pytest-fixtures` batches adjacency reads by breadth-first frontier. Its chain
excludes the root, deduplicates shared dependencies/cycles, and retains minimum
depth and parent-discovery ordering with name-sorted children. The optimization
changes database work, not which fixture relationships the index can resolve;
static fixture reachability remains distinct from running pytest.

## Health projections versus history metrics

`roam understand` and `roam capsule` use the shared health calculation but do
not compute the spectral-history value that their answers never expose.
Capsules retain all exported health fields in both file and stdout output,
including path-redacted exports. Health scores and their
inputs retain the same definitions. Snapshot collection still computes spectral
history by default; this optimization does not replace a slow or unavailable
measurement with a made-up value, or change stored history semantics.

## Practical command checks

`test-impact` distinguishes an unavailable Git changeset from a measured empty
one. Invalid revisions, missing Git, launch errors, and timeouts return exit 6;
JSON carries `state: "diff_unavailable"`, `git_error`, and `partial_success: true`.
SARIF marks the invocation unsuccessful and includes a diagnostic notification.
MCP preserves the same incomplete state. An emitted `count: 0` on that path
counts output rows, not proof that no tests are affected. Valid empty ranges
remain successful; even a populated selection is a bounded static mapping, not
test execution or complete coverage. Paths are read as NUL-delimited records
so spaces and non-ASCII names survive Git's output quoting rules.

```bash
roam doctor
roam orphan-imports
roam verify-imports
roam --detail algo
roam --json ai-readiness
roam --json tx-boundaries
roam partition --agents 4
git diff | roam critique
```

Global flags such as `--detail` and `--json` go before the command unless that
command also declares a local flag. Use `roam semantic-diff --base HEAD~1` for
the structural Git comparison. `magic-numbers` defaults to the current directory,
respects discovery exclusions, and includes monorepo workspaces; an explicit path
narrows the scan. Frequency alone does not make common numeric literals bugs.

`mcp-setup` previews configuration by default; add `--write` only when you intend
to install it. Use the platform names printed by `roam mcp-setup`, such as
`claude-code`, `codex-cli`, `cursor`, or `gemini-cli`.

`hotspots` analyzes ingested runtime traces. Static churn/complexity hotspots
in dashboards are a different metric; use `weather` to inspect that ranking.
`bus-factor` staleness is scoped to a directory's change history, not the person's
activity elsewhere. `ai-ratio` keeps legacy JSON keys for compatibility, but
`ai_ratio` and per-file `probability` are normalized heuristic scores; their names
must not be read as calibrated authorship probabilities.

For reproducible fixtures, private evidence placement, and test gates, see
[repository maintenance](../repository-maintenance.md).
