# Reliable CLI calls for agents

## Automatic Claude hooks

The working-tree `roam hooks claude --write` installer writes an executable plus
an argument list, avoiding shell interpretation of Python and hook paths.
It migrates the exact historical Roam command and keeps other hook entries;
customized hook bodies still require explicit review. Keep Roam and its
compile-code consumer in the same Python environment and update both when
adopting this command format. Exec-form dispatch has been exercised with
Claude Code 2.1.263; older-client support is not established here.
This describes the coordinated source change, not a package-release announcement.

A configuration check does not establish successful hook execution. Inspect
UserPromptSubmit and Stop events and complete verification receipts: the agent
process can exit successfully even when a hook failed to execute. See
[verification evidence](concepts/verification-evidence.md) for acceptance limits.

## Choose a command

Start with the question, then narrow the output. Roam's existing CLI and MCP
interfaces share the same analysis; a second wrapper is not required.

```sh
roam ask "where is grep_cmd defined?"
roam grep --help
roam --json grep "protocolVersion" --max-results 5 --context 2 --max-packets 3
```

Use `roam surface --json` to discover the installed command inventory and
`roam <command> --help` (or `-h`) for its actual options. Use `--help-all` for
the expanded top-level list. CLI options are not interchangeable with Unix grep:

| Roam option | Meaning |
| --- | --- |
| `--max-results N` / `-n N` | Positive result limit, default 50; not a line-number switch |
| `--source-only` / `-s` | Search source files only; not error suppression |
| `--test-only` / `-t` | Search test files only; not sort order |
| `--context N` / `-C N` | Attach 0–20 surrounding source lines |
| `--max-packets N` | Return at most 1–20 unique context packets, default 8 |
| `--max-packet-lines N` | Cap each packet at 1–400 lines, default 120 |

Prefer long options in generated commands. A rejected flag is not an analysis
result: inspect the help and retry with a supported option.

## Read totals separately from returned results

Grep's JSON summary exposes `total`, `shown`, and `omitted_matches`. With
`--group-by symbol`, it also exposes `total_groups`, `shown_groups`, and
`omitted_groups`. `--max-results` caps the match list and group list separately.
Each group contains a total hit count and up to three samples; it is not a
second full copy of every match. Text output shows up to two samples per group.

Context packets are generated from the returned match list, not every group.
Their summary separately reports omitted packets and truncated/unreadable
source. A result cap is a presentation limit, not evidence that the remaining
matches were checked or are unimportant. Narrow the pattern/glob before raising
limits. Source-only and test-only filters appear in non-empty verdicts.

Search execution errors are different from zero matches. Missing executables,
timeouts and non-success exit codes produce an incomplete/partial verdict with
`partial_success: true`. Valid matches emitted before an engine error are kept.
An unreadable path must not turn thousands of real matches into a clean empty
result. `refs-text` requires review after such a failure; `delete-check --ci`
refuses with exit 5 in text, JSON and SARIF, including when no surviving match
was confirmed. Fix the reported search error and rerun before approving removal.

### Read `refs-text` as reference evidence, not deletion permission

The working-tree `static_reference_evidence_v2` meaning is recorded in
`summary.verdict_definition`. This is a source contract, not an announcement
that an older installed package has been repaired.

- `SAFE-TO-REMOVE` retains its legacy name but means **no source-code matches
  in the selected text-search scope**, not approval to delete. Inspect any
  non-code hits and establish the scope needed for your change.
- `REVIEW` includes source hits with unknown reachability and hits not reached
  in the indexed graph. No indexed caller is not proof of no runtime caller.
- `LOAD-BEARING` retains observed indexed reachability/importance even if other
  references are unresolved. A positive finding is useful without being complete.

Read each result's `resolution`, `unresolved_code_references` and
`partial_success`, as well as the aggregate `summary.partial_success`.
With `--per-match-detail`, `reachable: null` means there is no enclosing indexed
symbol: the file may be omitted, its syntax unsupported, or the hit outside a
symbol span. It is not `false`. The unresolved count survives compact output
and budget-shortened detail in the summary. An unrelated unsupported file with
no matching reference does not invalidate a confirmed positive observation.

`summary.search_scope` records the engine's population and requested globs.
Git searches tracked nonbinary files; ripgrep uses its searchable-file rules;
the last-resort scan is limited to indexed files. Its `complete` flag describes
reported search execution, not whole-repository coverage or a file-count census.
Search completeness and graph resolution are separate. Match counts are observed
references, not files scanned. A missing scan population cannot establish broad
absence. Full JSON keeps the source/index freshness channel; compact output is
not a substitute for establishing freshness before acting.

An explicitly requested `--reachable-from` entry is resolved even when the text
search finds no matches. An unresolved entry returns `state: "unresolved_entry"`,
`partial_success: true` and exit 1; it does not turn into a completed negative.
With a valid entry, an empty search retains that anchor in its summary.
MCP `roam_refs_text` uses literal matching by default; `fixed=false` selects
the CLI's explicit `-E` regex mode. Omitting the CLI's `-F` option is not enough
to enable regex because literal matching is the default.

```sh
roam --json grep "initialize" --glob py --source-only --max-results 5
roam --json grep "initialize" --group-by symbol --rank-by importance --max-results 3
```

The [AXI design principles](https://github.com/kunchenguid/axi) informed these
improvements to help discovery, bounded output and explicit counts. This is not
a claim of AXI conformance, a new output format, or a promise that every short
flag matches another CLI. For evidence limits, see [detector evidence](concepts/detector-evidence.md).

## Check the work after an edit

`roam verify --auto` chooses checks for the files that changed. Depending on
the change, these include:

- **naming** — against the codebase's own per-language convention (sampled
  from production code only: test/vendored/generated files neither vote nor
  get flagged, framework lifecycle names like `setUp` are never touched)
- **imports** — check that imports resolve to
  the index, the stdlib, or a declared dependency. A module path that
  resolves to nothing fails as a likely hallucination; near-miss names get
  fuzzy did-you-mean candidates
- **error handling / syntax / complexity / cycles / duplicates** — scoped
  structural review with honest disclosure when any sub-check could not run
- **secrets** — a leak gate over every touched file: credential shapes
  (cloud keys, tokens, PEM blocks) fail the check, and an optional
  repo-local `.roam-leak-patterns.py` catalogue catches the strings *your*
  project must never publish
- **patterns** *(advisory, `--deep`)* — the algorithm/idiom catalog scoped
  to the diff: N+1 query shapes, loop-invariant calls, string-concat loops,
  each with a candidate approach and a fix sketch

**The fix loop.** Wired via `roam hooks claude --write`, findings come back
to the agent as an actionable list — *fix, then re-verify* — and the gate runs
again on each correction (Claude bounds consecutive continuations). A
human-reviewed exception can be recorded in `.roam-suppressions.yml`, keyed by
**symbol** so it survives refactors that shift line numbers; automatic hook
corrections cannot alter suppressions, policy, baselines, or verification
scope. The compile hook remains fail-open. The edited-turn Stop gate is quiet
only on a complete PASS and blocks when verification is unavailable,
malformed, incomplete, or reports non-advisory findings.

To focus on your current change or manage findings already in the project:

```bash
roam verify --auto                      # changed files, auto-selected checks
roam verify --diff-only                 # only lines you changed vs HEAD
roam verify --changed-lines cli.py:40-90   # exact ranges (agent harnesses)
roam verify --baseline-write            # snapshot current findings as accepted debt
roam verify --new-only                  # then: only NEW findings fail
roam verify --report --severity fail    # whole-repo ranked punch-list (non-gating)
roam verify --off / --on               # pause / resume the loop repo-wide
```

**The commands that run beside it** in the same post-edit stance:

| Command | Role in the loop |
|---|---|
| `roam verify-imports --path src/roam/cli.py` | Check import resolution in one file |
| `roam delete-check --ci` | Gates a deletion diff on surviving references (exit 5 on BREAK-RISK or an incomplete check) |
| `git diff \| roam critique` | Clones-not-edited check + blast radius on the patch (exit 5 on high severity) |
| `roam verify --report --persist` | Writes findings to the registry so the **compiler** embeds them as `known_findings` in future envelopes — debt gets fixed opportunistically |

The test suite includes deliberately broken examples, clean examples that
should not be flagged, and checks that saved exceptions survive refactoring.
Those tests help prevent regressions; they do not measure every detector's
accuracy on unfamiliar repositories. [Detector evidence and limitations](concepts/detector-evidence.md)
explains where findings still need careful review.
