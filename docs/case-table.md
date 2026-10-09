# Propose cases before an edit

Run `roam case-table <symbol>` to propose a known-answer table for a decision
or classification function. Review the input dimensions and fill each
`expected_answer` independently before changing code. The reported current
branch and return are observations, not an oracle for correct behavior.

```sh
roam case-table classify
roam --json case-table classify --max-rows 64
roam case-table classify --pairwise
roam case-table normalize --call-sites
```

Python and JavaScript/TypeScript functions use indexed symbol locations and
call edges, with local source parsing for annotations, comparisons, boolean
conditions, null/undefined, enum members, and return/raise/throw paths.
Local enum declarations and literal annotations provide bounded candidate
values. Literal comparisons add partitions; `{"kind":"other"}` represents
values outside the observed comparisons and needs concrete representatives.
Unconstrained parameters use `{"kind":"unknown"}`. Imported enum definitions
and computed expressions may remain unknown. Type annotations describe the
proposed domain; they do not prove callers respect it.

The default is a cross-product with at most 64 emitted rows; `--max-rows`
accepts 1 through 256. Coverage reports the theoretical product separately
from emitted rows. `--pairwise` constructs rows covering every pair of
candidate dimension values without materializing the product. It reports
`pairwise_complete: false` when the cap prevents complete pair coverage.
Pairwise cases can miss interactions involving three or more inputs.

`--call-sites` proposes one row per indexed incoming helper call, grouped by
caller kind (including tests). It supports reviewing a property required at
every call site; it does not automatically prove that property. Calls absent
from the index, dynamic dispatch, and unresolved references remain outside
its scope.

`test_call_observed` means an indexed direct test call supplies statically
recognizable arguments matching that row. `no_test_observed` marks a proposed
gap: no matching direct test call was established. This is static evidence,
not runtime coverage. A test-map relationship alone does not establish input
or branch coverage; indirect tests, parameterized cases, and computed arguments
may exercise additional rows. Caller rows distinguish calls originating in
tests from production calls. Source errors and degraded symbol resolution
set `summary.partial_success`; unsupported languages and missing symbols
return typed, nonempty envelopes.

The JSON envelope contains `dimensions`, `rows`, `coverage`, `caller_groups`,
`test_map`, and executable `next_commands`. Each input row includes current
branch decisions, an outcome (or `unknown`), direct test evidence, and an empty
expected answer. Loops, assignment-dependent conditions, effects, and complex
control flow are reported conservatively. Source code is never executed.

`invariants` discovers implicit contracts; `path-coverage` traces untested
call-graph paths; `test-gaps` prioritizes unprotected symbols; `calc-golden`
replays historical calculation answers. `case-table` supplies the proposed
input cases to review before an edit. The MCP tool `roam_case_table` exposes
the same options in the full preset.
