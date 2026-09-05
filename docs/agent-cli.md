# Reliable CLI calls for agents

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

```sh
roam --json grep "initialize" --glob py --source-only --max-results 5
roam --json grep "initialize" --group-by symbol --rank-by importance --max-results 3
```

The [AXI design principles](https://github.com/kunchenguid/axi) informed these
improvements to help discovery, bounded output and explicit counts. This is not
a claim of AXI conformance, a new output format, or a promise that every short
flag matches another CLI. For evidence limits, see [detector evidence](concepts/detector-evidence.md).
