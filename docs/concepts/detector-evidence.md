# Interpreting detector evidence

Search-backed deletion checks also depend on a complete search: `grep` preserves
partial matches and reports engine failures, `refs-text` downgrades unconfirmed
absence to `REVIEW`, and `delete-check --ci` refuses incomplete searches in every
output channel. See the [agent CLI guide](../agent-cli.md).

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
an empty or short finding list. For `path-coverage`, depth pruning and shared
visited-node traversal prevent an exhaustive-path claim even when no test gaps
are reported. Its test signal is static, not measured runtime coverage.

## TypeScript and browser projects

Relative runtime imports such as `./worker.js` can name TypeScript source.
The import checks share extension substitution: `.js` can resolve to `.ts`,
`.tsx`, or `.d.ts`; `.mjs` to `.mts` or `.d.mts`; `.cjs` to `.cts` or `.d.cts`.
The extension is replaced, not appended. These rules follow the
[TypeScript module reference](https://www.typescriptlang.org/docs/handbook/modules/reference.html#file-extension-substitution).
They do not implement all compiler options, package `exports`, project references,
or custom loader behavior. Keep `tsc --noEmit` and the project's actual build in
the validation loop.

`verify-imports` recognizes declared Node dependencies, including workspace
manifests, separately from local imports. `orphan-imports` treats bare package
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

## Review an algorithm finding

1. Open the exact reported source location and read `reason`,
   `evidence.matched_patterns`, and `evidence.context_lines` when supplied.
2. Check the assumptions: does the lookup collection vary, is a loop bounded,
   must asynchronous calls remain ordered, and does recursion revisit the same
   subproblem? A matching method name alone is insufficient.
3. Measure and run the project's tests before applying the proposed change.

The index distinguishes calls on other objects from direct self-calls and treats
loop-local assignments as varying inputs. Spread-accumulator and serial-await
checks require the matched operation inside the loop body. A power-like name and
ordinary multiplication are not sufficient for repeated-exponentiation advice.
These checks reduce false positives; they do not prove loop invariance, purity,
overlapping recursive subproblems, or safe parallelization in every program.

Refresh derived signals with `roam index --force` after installing a detector
change. Old indexed signals and newly read source can otherwise disagree.

## Graph scope and work planning

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

## Practical command checks

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
