# Understanding roam-code

This is the orientation document for anyone, human or agent, who works on or
with roam-code. `AGENTS.md` says how to build it. This page says what it is,
why it is shaped the way it is, how its output must be read, how we talk about
it, and how it got here.

Written 2026-09-06 from the public docs, the source tree, and private working
records, then reviewed by independent adversarial rounds against the same
bytes. Public files are cited as `file:line`; line numbers are from the
working tree on that date and will drift. Statements marked *[internal]* come
from private records that are not shipped. This page describes the working
tree; the published package is whatever the newest tag says, and behaviour
that landed after that tag is not yet in what `pip install` gives you.

## 1. The one card

**Roam is mechanical, instant codebase power, built for coding agents.**

Each word, defined honestly:

- **Mechanical.** The static analysis is deterministic. There is no LLM SDK
  imported anywhere under `src/roam`. Two paths reach a model, both opt-in
  and both outside the static checks: MCP sampling, where the server asks
  the *client's* model only when the client supports it and
  `ROAM_AI_ENABLED=1` is set (`src/roam/mcp_extras/sampling.py:128-134`), and
  the benchmark command `roam bench-compile`, which launches `claude -p` per
  cell by design (`src/roam/commands/cmd_bench.py:94-101`). `roam ask` is "a deterministic
  intent classifier over a small recipe book" (`src/roam/ask/__init__.py:5`);
  `roam plan` and `roam compile` ship "zero model calls"
  (`src/roam/plan/__init__.py:5`). The learned reranker in `roam retrieve` is
  a local gradient-boosted model.
- **Instant.** The word is in the project's first README (commit
  `12befe60`, 2026-02-09): "1 command. Instant. Zero round-trips." Today it
  means one index, then queries against
  it. Lightweight index queries are often under half a second on a warm
  index; broader analyses can take much longer, and CLI process startup alone
  can cost about 1.5 seconds on a slow Windows host (`README.md:849-853`).
  "Measure on your own machine before gating on these."
- **Power.** 287 commands, 246 MCP tools, 28 languages, one SQLite file. The
  breadth is deliberate: static power that is reusable, fast, and cheap, as
  many mini-tools as the software-engineering spectrum needs, rather than a
  small curated set *[internal design record, 2026-08]*.
- **For agents.** "Your agent writes the code. Roam gives it the bigger
  picture." (`README.md:5`). "Roam is agent-first: agents use its local
  codebase context and static checks as they work, while people set direction
  and decide what ships" (`docs/website-maintenance.md:16-18`). The MCP
  server's own instruction line states the design intent, not a measured
  comparison: "One tool call replaces 5-10 Glob/Grep/Read calls."

Why mechanical: the owner's standing rule for every flow is to ask whether a
question can be answered mechanically before making a model call, because the
mechanical path usually exists and is not obvious *[internal, 2026-08]*. Roam
is where that rule became code. Structural knowledge that can be checked
hardens into a detector, and a detector fires or it does not, which makes it
cheap to run and, by design intent, hard to game.

## 2. What it is, in one screen

The public identity, verbatim (`pyproject.toml:12`, identical in
`codemeta.json:6`):

> Local codebase intelligence for AI coding agents: structural repo map,
> change-safety gates, MCP security receipts, and tamper-evident
> ChangeEvidence packets. Local analysis, zero API keys, no source-code egress.

The category: **local codebase intelligence and assurance for AI-assisted
software change**. Roam turns repository structure into **maps, gates, and
evidence**:

| Layer | What it is | Where in the code |
|---|---|---|
| Maps | symbols, calls, imports, dependencies, layers, clusters, git history, tests, effects, taint summaries, in 29 SQLite tables | `src/roam/db/schema.py`, `src/roam/index/indexer.py` |
| Gates | deterministic pre- and post-change checks: blast radius, affected tests, clones, algorithmic risk, architecture drift, rules, conventions | `src/roam/commands/cmd_preflight.py`, `cmd_impact.py`, `cmd_critique.py`, `cmd_verify.py`, `src/roam/rules/` |
| Evidence | tamper-evident records of what was checked: proof bundles, run ledger, ChangeEvidence packets, MCP decision receipts | `src/roam/proof_bundle.py`, `src/roam/runs/`, `src/roam/evidence/` |

Review is a workflow. Roam is the local intelligence and assurance layer
underneath the workflow. It is not code search, not an AI PR reviewer, not
lint, not SAST, not agent orchestration, and not compliance tooling. Its
answer to each of those is "local graph + judgment + evidence". The moat is
not command count; it is the combination of a local deterministic code graph,
algorithmic and architecture judgment, an agent-facing MCP surface,
tamper-evident change evidence, and principle lenses grounded in the repo's
own signals.

### The pipeline

`roam index` runs one orchestrator (`src/roam/index/indexer.py`) in phases:
parse and extract symbols per file; resolve references into edges, with
framework post-resolvers and cross-language bridges; graph metrics (PageRank,
centrality); git history and Louvain clustering; effects and taint; health and
cognitive load; search indexes; then an `index_manifest` row recording
versions and step status. Index-backed commands follow one template:
`ensure_index()` → `open_db(readonly=True)` → query → `json_envelope(...)` or
text with a leading `VERDICT:` line (`AGENTS.md:299-322`). A few read other
sources (`roam commands` builds from manifests) or write (`roam vuln-map`
ingests into the index).

### The output contract

Default `--json` responses are `roam-envelope-v1` envelopes
(`src/roam/output/formatter.py:19-20`) with `schema`, `schema_version`,
`command`, `version`, `project`, `summary`, an auto-derived `agent_contract`,
and `_meta`. `--agent` switches on compact output, which drops the identity
and `_meta` keys and keeps `summary` (`src/roam/cli.py:2044-2052`,
`formatter.py:1853-1896`). Timestamps and index age live under `_meta` so content keys stay
byte-stable for prompt caching (`formatter.py:1703-1706`), with two known
exceptions: MCP sampling adds a model-written `briefing` at top level
(`src/roam/mcp_extras/sampling.py:202-217`), and attestation payloads carry
their own timestamps.

`summary.partial_success` is present on every analysis envelope (schema-only
outputs such as `roam savings --schema` carry an empty summary). `truncated: true` has three
shapes, and only the first implies `partial_success`:

| `truncation_reason` | Cause | Recovery |
|---|---|---|
| `budget` | involuntary, the default 20,000-token JSON budget (`formatter.py:625-653`, `:1794-1809`) | `--budget 0` |
| `detail_mode` | intentional elision of detail lists; `partial_success` untouched (`formatter.py:2094-2125`) | `--detail` |
| absent | a command-level display limit such as `roam commands --limit`; `partial_success` stays false (`src/roam/commands/cmd_commands.py:78-117`) | the command's own limit flag |

Treat a missing or unrecognised reason as incomplete.

Over MCP, two more shapes appear. A large result comes back as a handle
envelope (`schema: roam-code.com/spec/handle/v1`, `is_handle: true`,
`fetch_with`); fetch it with `roam_fetch_handle` before treating it as
analysis (`src/roam/mcp_server.py:3656-3680`). A failed call comes back as a
structured error inside a successful JSON-RPC result, with `isError`,
`status`, and `error_code`; check those before reading `summary`.

Exit codes (`src/roam/exit_codes.py`): 0 success · 1 error, including a
version-incompatible index · 2 usage, and also `roam doctor`'s blocking
environment check · 3 index missing, only when `ROAM_NO_AUTO_INDEX` is set ·
4 `needs_review` from the guard family · 5 gate failure · 6 partial · 130
interrupted. Under a gate flag, exit 5 fires on a measured violation **or** an
incomplete measurement: "An absent measurement is UNKNOWN, never a benign
CLEAN" (`exit_codes.py:193-206`). `roam exit-codes` prints the table from
source.

### The surface

| Quantity | Value | Source |
|---|---|---|
| CLI commands | 287 (280 canonical + 7 deprecated aliases), 7 categories | `roam surface --json`; `README.md:16` |
| MCP tools | 246 registered; the default `core` preset exposes 16 plus the always-on `roam_expand_toolset` | `README.md:16`; `src/roam/mcp_server.py:150-171`, `:355-356` |
| MCP presets | core, review, refactor, debug, architecture, compliance, compile-curated, full | `mcp_server.py:240-353` |
| Languages | 28 counted; 73 extensions map to 43 language ids; 23 extractor modules including the generic tree-sitter walker that covers the rest | `src/roam/index/parser.py:20`, `src/roam/languages/*_lang.py` |
| Tests | ~1,540 `test_*.py`, ~500 of them `test_w<n>_*` | `tests/` |

Counts in prose drift. The authoritative numbers come from `roam surface
--json` and the `auto-count` marker blocks that `dev/build_readme_counts.py
--check` maintains. Do not type a count into copy.

### What leaves the machine

Nothing by default. The full inventory is `docs/network-boundary.md`; the
named exceptions (`:15-23`) are: the first parse of a language downloads a
checksum-verified grammar bundle; `roam version --check`, `roam metrics-push`,
and the GitHub check-run poster are explicit online commands; `roam
bench-compile` launches `claude -p`; `roam pr-analyze
--diff-from-pr` and `roam pr-replay --github-reviews-gh` fetch through `gh`;
`roam stale-refs --check-external` sends requests to URLs found in files, and
a repository config file can enable it without a flag; keyless signing talks
to OIDC, Fulcio, and Rekor; MCP summarisation sends the report to the
client's model when `ROAM_AI_ENABLED=1`; and `roam verify` can run the
repository's own test command, which does whatever that command does. A
connected agent may send tool results to its provider; Roam's local analysis
is not a promise that the entire agent workflow stays offline
(`docs/website-maintenance.md:27-29`).

### The MCP security posture

Roam is the evidence producer underneath a gateway, never the gateway
(`dev/MCP-SECURITY-POSTURE.md:70-71`). Of the five controls a gateway buyer
asks about, roam owns two, per-role permissions (the four modes) and audit
logs (HMAC-anchored receipts plus the run ledger); it contributes structural
argument inspection, a dry-run mode, and regex secret and injection-marker
scanning; a gateway owns cross-server shadow rollout and semantic content
scanning (`:82-88`). Roam does not issue tokens, isolate tenants, correlate
across servers, or remediate (`:291-322`). The receipt's closed enums are
append-only under v1; the exported JSON Schema carries the `$id`
`https://roam-code.com/schema/mcp-receipt/v1.json` (`:395-413`).

### Relationship to compile-code

compile-code calls itself "a compiler for AI coding tasks" and "a thin CLI
over the roam-code engine (installed automatically)". It pins
`roam-code >= 13.10.0, <15`, runs Roam's compiler before an agent's first
token and Roam's verifier over the edited targets afterwards (its driver
delegates `--json verify --auto -- <targets>`, falling back to `--changed`
when no target is bound), and refuses to make an assurance claim when the
installed roam is below its floor. Roam selects and builds the
compile artifact (`src/roam/commands/cmd_compile.py:287-296`); compile-code is
the driver that runs Roam's compiler before the prompt and its verifier after
the edit, and wires the hooks. The `compile-curated` MCP preset
(`src/roam/mcp_server.py:341-351`) is the shared contract and "MUST stay in
sync" with compile-code's curated tool list.

## 3. How an agent should use it

### Gate on the envelope, never on the exit code

This is the single most important operational rule, learned the hard way by
a downstream consumer *[internal, 2026-08-23]*:

- The exit code does **not** carry not-found, skipped, stale, or truncated
  state. `roam symbol <absent> --agent` exits 0 with
  `{"resolution": "unresolved", "state": "not_found", "partial_success": true}`.
  `roam critique` with one of three checks skipped exits 0 when no finding is
  high severity; it exits 5 on any high-severity finding regardless of flags
  (`src/roam/commands/cmd_critique.py:1500-1501`).
- `--ci` is a **global** flag on `roam` and must precede the subcommand;
  `roam critique --ci` is a usage error. Some commands also define their own
  local `--ci` (for example `roam taint --ci`,
  `src/roam/commands/cmd_taint.py:459-464`); read `roam <command> --help`
  before assuming either form.
- The index can be stale with exit 0. `_meta.index_age_s` is only seconds
  since the database file changed (`formatter.py:1819-1826`). Source
  freshness is `_meta.index_status`, stamped on stale-sensitive commands when
  the index no longer matches the repository (`formatter.py:1613-1629`). Its
  absence does **not** establish freshness: the producer returns nothing when
  git or index information is unavailable or `ROAM_NO_STALENESS_HINT` is set
  (`src/roam/commands/resolve.py:166-201`). Run `roam index` after a pull,
  branch switch, or commit.
- The default budget truncates. `roam agent-plan` returned `summary.tasks: 3`
  with `emitted_counts.tasks: 1` and `truncated: true`. A caller reading
  `tasks[]` alone would plan one third of the work.
- Review evidence must be bound to the diff you mean. With empty stdin,
  `roam critique` silently selects a review target itself: working-tree
  changes first, then the previous commit (`cmd_critique.py:936-947`,
  `:240-277`), so a failed upstream `git diff` yields a complete-looking
  review of a different change. An omitted `--intent` uses HEAD's subject
  even for a supplied diff (`:1053-1066`). Pipe the diff only after the diff
  command succeeded, pass the intent, and check `summary.review_source` is
  `piped_diff` or `input_file` (`:932-944`).

The safe form, in order:

1. Use `--json`, pipe the exact diff after confirming the diff command
   succeeded, pass `--intent`, and check `summary.review_source`.
2. Treat exit 3, 4, 6, any `truncated: true`, `partial_success: true`, a
   present `_meta.index_status`, and any `check_status` entry that is not
   `"ran"` as **UNKNOWN**, never as a pass. Refresh the index yourself rather
   than trusting the absence of a staleness stamp.
3. Name the checks you expected and reject a result that does not carry them.
4. Then read the findings, their severity, and the verdict. A check that ran
   is a measurement, not permission: a completed `critique` with medium
   findings exits 0.

Exit 5 under a gate is a clean mechanical refusal, and it can mean either a
violation or an incomplete scan; both are non-pass.

### Absent state is named, never coerced

Roam's own quality rule (`AGENTS.md:45-48`): never emit `verdict: "SAFE"` when
a check did not run; say `state: "not_initialized"`, not `"broken"`. Oracles
are tri-state, `true | false | null` with a `reason_class`
(`src/roam/commands/cmd_oracle.py:12-16`). A test record with no status is
`unverified` and "can never satisfy a required check"
(`src/roam/guard_enums.py:66-79`). "A failed scan is unknown, not a measured
empty set" (`docs/concepts/verification-evidence.md:111`). Carry that
discipline forward when you consume roam output.

### Which commands to reach for

CLI: navigation and structure (`uses`, `context`, `symbol`, `search`,
`disambiguate`, `deps`, `file`), pre-edit safety (`preflight`, `impact`,
`safe-delete`, `delete-check`, `affected-tests`, `test-impact`), and
structure judgment with good signal-to-noise (`complexity`, `clones`,
`dark-matter`, `vibe-check`, `weather`, `debt`, `bus-factor`). For a diff,
`critique` is the command with the graph-aware checks. The skill file
`skills/roam/SKILL.md` carries the situation-to-command table.

MCP: the default `core` preset exposes `roam_ask`, `roam_understand`,
`roam_search_symbol`, `roam_uses`, `roam_prepare_change`,
`roam_diagnose_issue`, `roam_batch_search`, `roam_coupling`, `roam_deps`,
`roam_grep`, `roam_fetch_handle`, `roam_alerts`, `roam_dead_code`,
`roam_taint`, `roam_file_info`, `roam_metrics`, plus `roam_expand_toolset`
(`src/roam/mcp_server.py:150-171`). `roam_context`, `roam_preflight`,
`roam_impact`, and `roam_critique` live in the wider presets; restart with
`ROAM_MCP_PRESET=review` (or another) to get them.

### Limits an agent must respect

The measured record behind these, with dates and percentages, lives in the
private companion; the rules themselves are public.

- **Own-fixture precision does not transfer.** A detector's 1.0 on its own
  fixtures says nothing about a repository it has never seen. On unfamiliar
  code the dominant failure is role and context blindness: example code
  treated as production, library public API treated as app-internal,
  domain-inapplicable detectors running instead of abstaining. The value of
  a cold run on a well-maintained stranger repo is routing signal, not defect
  catching, and a well-maintained repo would yield few defects even for a
  perfect tool.
- `taint` runs its own source-to-sink rule packs against the index and
  project source (`src/roam/commands/cmd_taint.py:652-664`); its evidence is
  advisory reachability plus a common-caller check, and only a same-function
  Python path-sensitive pass is emitted as computed dataflow
  (`src/roam/security/taint_engine.py:804-823`). Treat its findings as leads
  to rank and inspect, never as a scanner's verdict.
- `health` is a review aid, not a label. "A health score is not permission to
  merge" (`README.md:65-66`).
- `roam verify`'s SYNTAX check parses Python with `ast.parse` and the other
  supported languages with tree-sitter error nodes; data and stand-in
  grammar languages are skipped (`src/roam/commands/cmd_verify.py:1953-1978`,
  `:2047-2055`, `:2130-2161`). A tree-sitter parse accepts constructs a real
  compiler rejects. Corroborate with the real toolchain.
- `roam clones --persist` must run before clone-aware checks. `critique` and
  `oracle is-clone-of` qualify the saved scan: filtered, capped at 2,000
  functions, incomplete, or stale scans carry `partial_success: true`.
  The oracle keeps positive saved pairs visible but answers indeterminate on
  unmatched names until a current completed scan supports a bounded negative.
  See [detector evidence](concepts/detector-evidence.md#clone-scans-and-patch-review).
  Retrieval still reads pairs without checking scan metadata.
- `partition` auto-caps at 8 partitions; `conventions` standalone is noisy on
  large identifier sets; `pr-analyze` reads `--input` and stdin but `critique`
  has the graph-aware checks.
- What detectors can and cannot establish is the whole of
  `docs/concepts/detector-evidence.md`; read it before gating on a finding.

When you add or promote a detector: report false-positive rate per finding
kind, never blended per command, as FP over TP plus FP plus arguable; have
two labellers score a stratified sample and label each item TP, FP, or
arguable with a `file:line` evidence cell; and treat a false-positive class as general only
after it reproduces on a second repository of a different shape or language.
Heuristic-tier detectors carry no precision target and are never CI gates.

### Traps

- Bare `roam` may resolve to a stale global install. Use the editable build in
  the venv, and let `roam doctor` tell you when a sibling install shadows it.
- `roam commands` lists the **target project's** runnable commands from its
  Makefile or package.json, not roam's CLI. Use `roam surface --json` or
  `roam --help-all`.
- Exit 4 means `needs_review`, not "index stale".
- A version-mismatched index refuses at read-only open with exit 1; rebuild
  with `roam index --force` (`src/roam/db/connection.py:1159-1169`).
- The first index reads a 365-day git window. Older history is absent from
  churn and bus-factor unless `ROAM_GIT_SINCE` is set.
- Switching MCP presets requires restarting the server with
  `ROAM_MCP_PRESET=<name>`; `roam_expand_toolset` only reports. The default
  preset is `core` on purpose; the wider presets exist for named workflows.
- Auto-index is on by default; set `ROAM_NO_AUTO_INDEX=1` in CI to get a
  structured exit-3 refusal instead of a surprise build.

## 4. How we speak

The voice is a product decision, enforced by lint where it can be.

### Say

- local codebase intelligence · agentic assurance · engineering principles
  made observable · proof-carrying PRs · local graph + judgment + evidence
- "maps to", "supports evidence for", "audit-ready record"
- "deterministic facts, not model guesses"
- "Semantic reviewers read what the code does. Roam reads what it touches."
- "Your source never leaves the machine", with the caveat that a connected
  agent may send tool results to its own provider
  (`docs/website-maintenance.md:27-29`)

### Never say

- "certifies", "compliant", "guaranteed", "guarantees". The wording lint
  checks generated reports, docs, and commit messages
  (`CONTRIBUTING.md:457-467`); the word list and the sole allowed negation,
  "does not certify", live in `src/roam/commands/cmd_service_report.py:49-51`.
- "We find bugs your scanner misses." We rank its findings; we do not
  out-detect it.
- "Proof of who did it." Signed records give integrity, not authenticity.
- "Real taint tracking" or parity with CodeQL, Semgrep, or Snyk.
- "Roam caught a defect" on a repository it was not tuned for.
- "Safe to ignore" for a non-reachable finding. Non-reachable is not safe.
- Any absolute security claim ("prevents all secret leaks", "fully sandboxed").
- "Another AI PR reviewer", generic SAST or SCA positioning, "SOLID score",
  command-count-first headlines.
- Marketing superlatives, manufactured scarcity, fake urgency.
- Internal session shorthand in public text: phase and round numbers,
  polish-speak, customer names (`tests/test_no_internal_language.py`).

### Numbers

A public claim is an instrument reading: no new number ships unless a
shipped artifact reproduces it on demand, and a doc is not an artifact.
Numbers already published stay as dated historical records with their
kernel and caveats attached; they are neither re-run on every release nor
rewritten. In practice:

- Every measurement carries date, kernel version, n, and its caveat
  (`README.md:176-258`).
- Losses are published beside wins and labelled as losses
  (`README.md:248-249`). That habit is worth nothing if a reader runs the
  documented command and gets a different answer.
- Confidence intervals are quoted where they exist. "10 of 10 in both arms"
  is not parity; its Wilson interval is 72 to 100 percent.
- Historical numbers are frozen. "Rewriting one falsifies a result. Never
  sync these." (`docs/releases.md:32-36`).
- LLM-judged quality is not a public instrument reading. Keep the cost figure,
  label the quality "directional", and say which model judged.
- No counts in prose. Counts come from generated marker blocks.

### Shape

- Short declaratives, one claim per sentence. The recurring pattern is
  "X establishes A. It does not establish B." (the evidence-levels table in
  `docs/concepts/verification-evidence.md:117-125`).
- Fence scope with "A is not B": "A suggested test list is not test coverage,
  and a good health score is not permission to merge." (`README.md:65-66`).
- Imperatives to the reader for every action. Tool descriptions are
  imperative ("Run X", not "This command").
- Command output is plain ASCII: no emoji, no colour, no box drawing
  (`AGENTS.md:489`).
- Every product spec and paid deliverable opens with what is *not* built or
  *not* covered, and degrades to "not available" rather than backfilling an
  illustrative number.
- Working memos open with a status banner (research, design, or measurement),
  mark each item SHIPPED, BLOCKED, or TBD, prefix each issue SUSPECTED or
  CONFIRMED, and carry an evidence cell (kind, `file:line`, snippet) per
  claim. A published historical record is never rewritten; a superseded
  working memo is either folded into an active doc and pruned, or stubbed
  with a pointer so it is not rediscovered as live.
- Single-operator honesty: dated slots, not SLAs; a response-time note beside
  any contact CTA.

### Public surfaces and their jobs

README is the first ten minutes: category, install, three workflows, then
depth. The homepage is one category, one hook, one install CTA. The docs home
routes and does not repeat the pitch. Compare is a category comparison, not
only a vendor table. Pricing says what is available now versus planned.

### The comparison frame

Most adjacent tools are complements, and we say so:

- Cursor, Windsurf, Claude Code, Codex edit and execute. Roam gates and
  records.
- CodeRabbit, Greptile, Qodo review semantics. Roam reviews structure, blast
  radius, and evidence. Position as the layer beneath review, never as a
  replacement.
- SonarQube, Semgrep, CodeQL enforce static and security rules. Roam adds
  local graph context beside them; its vulnerability importer reads
  npm-audit, pip-audit, Trivy, OSV, and generic package advisories
  (`src/roam/commands/cmd_vulns.py:315`), not SARIF from those scanners.
- Sourcegraph and Cody do cross-repo intelligence. Roam's capability is a
  persistent local graph with graph algorithms, git history, and the
  proof-carrying change workflow.
- Serena and LSP-based servers have compiler-grade type resolution that roam
  lacks.

## 5. What has been measured and published

The honest forms of the numbers the README carries. The raw A/B cells are
private and are not shipped (`README.md:236-242`); the ledger is not re-run
on every release, and each row is measured at the stated kernel.

| Claim | Honest form |
|---|---|
| Compile envelope A/B, June 2026, 41 cells, kernel 13.4, one served model: −83% turns, −80% input tokens (`README.md:183-186`) | Deltas are medians over all 41 cells (n=2-3 per cell); state the aggregation whenever the number is quoted. |
| Second model, same navigation tasks | −33% turns overall; the best single cell hit −88% (`README.md:188-189`). Quote the aggregate, not the best cell. |
| Routing replay (`README.md:231-234`) | 57% of envelopes ship the literal answer, estimated from a sample of the 723-prompt corpus; the facts share is a separate measurement, not the complement. Do not quote a higher pre-executed share. |
| Bug bench 10/10 both arms, −13% cost, n=10 (`README.md:222-228`) | No difference detected at low power (95% CI [72%, 100%]). The −13% is a dollar figure; quote it with the token figure beside it. |
| Repair-intent retrieval nDCG@10 +0.064 [+0.032, +0.097] (`README.md:452-463`) | Versus lexical search, on real patches, repair-sibling scope, 576 fixes across 12 third-party repos; recall@10 improvement not significant. The one preregistered, held-out, stranger-repo result. |

What sits under every capability: the call graph's own resolution precision
and recall have not been published on a labelled benchmark. Blast radius,
reachability, taint, and effects are all downstream of it. Measuring it is
the highest-leverage credibility move on the board.

## 6. How we got here

- **2026-02-09.** First commit, already Python, already "instant codebase
  comprehension for AI agents", MIT-licensed, versions 1.0 through 11.x in
  three weeks of February. Early issues asked for C#, YAML and HCL, ignore
  patterns, and a rename of `roam math` to `roam complexity`.
- **2026-05.** v12 through v13.4: over sixty dated releases in one month
  (`CHANGELOG.md:852-9321`), the dogfood corpus that produced the six anti-patterns in
  `AGENTS.md`, the Agent-OS substrate (runs, modes, leases, constitution,
  permits), the MCP runtime-security wave (receipts, redaction, mode gating),
  and the strategy that fixed the category and the wording guard.
- **2026-06.** v13.5 and v13.6: the task compiler and the compile A/B; the
  JavaScript and TypeScript idiom pack.
- **2026-07.** v13.7 through v13.10: the stranger-repo tests that showed
  own-fixture precision does not transfer, the fixes that followed, the
  claims audit, the `internal/` privatisation ("one private folder, no
  pattern magic", `AGENTS.md:29-36`).
- **2026-08-07.** v14.0.0, then the `-h` flag-collision and MCP
  spec-conformance issues (#100, #106).
- **2026-09-05.** v14.0.1 to v14.0.4 in one day: packaging fixes, the proof
  schema shipped in the wheel, and the homepage redesign. This page followed
  on 2026-09-06.

Commit volume peaked in July 2026 (`git log --format=%ad --date=short`,
counted by month on 2026-09-06); the open issues on 2026-09-06 asked for official container images, an
agent-exchange interface, and MCP spec conformance.

## 7. Vocabulary

- **envelope** — the JSON response shape; also, in the compiler, the context
  block attached to a prompt.
- **handle** — the MCP stand-in for a large result; fetch it before reading.
- **verdict** — the `VERDICT:` line of text output (by convention first;
  `critique` prints its review disclosure before it) and `summary.verdict`;
  must work alone. In the guard family, one of `pass | pass_with_warnings |
  needs_review | blocked`, mapped to exit 0/0/4/5.
- **partial_success** — set whenever any sub-check failed, was skipped, or
  the output was budget-truncated. Never a silent SAFE.
- **refusal** — a structured non-answer with a reason: exit 3 without an
  index, exit 5 from a gate, a parser rejecting malformed input. "A parser
  refusal is not a verdict."
- **UNKNOWN / unverified / not_evaluated** — the third state. Never a pass.
- **evidence** — what was actually checked, with its limits stated. The
  assurance frame is identity + authority + evidence.
- **proof bundle** — `AgentChangeProofBundle v1`: command graph (what can
  run) + verification contract (what must run) + executed and missing checks +
  verdict. It records available evidence; it does not authenticate claims.
- **ChangeEvidence** — the portable packet with content hash, redaction
  metadata, and links to the run ledger.
- **run ledger** — the per-run HMAC-chained event log under `.roam/runs/`.
  The separate audit trail is a plain hash chain: tail tampering is caught
  only when a head-pointer file exists, and an attacker who rewrites both the
  trail and its head pointer is not detected
  (`src/roam/commands/cmd_audit_trail_verify.py:20-29`).
- **receipt** — `McpDecisionReceipt`: actor, tool, input hash, policy
  decision, output hash, redact-on-egress.
- **mode** — `read_only → safe_edit → migration → autonomous_pr`, the declared
  action surface, enforced at the MCP boundary for sensitive tools.
- **preset** — an MCP tool subset selected at server start.
- **oracle** — a closed yes/no/indeterminate question command.
- **blast radius** — code reachable through indexed connections from a
  change; "it does not mean all of those functions will break".
- **findings registry** — the cross-detector table in the index, rows
  confidence-tagged `static_analysis | structural | heuristic | runtime`.
- **advisory** — findings to review, not gate on, because blind precision on
  unfamiliar repos is not yet measured.
- **principle lens** — a detector that turns an engineering principle into a
  local signal. It ships only when five gates pass: a local signal exists,
  false positives can be bounded, the next action is concrete, evidence can
  be attached, and a buyer or agent cares.
- **W-id** — a sequential work-item number. A test file carries it
  (`tests/test_w603_*.py`), its docstring opens with it, the source cites it
  where the fix lives, and CHANGELOG entries reference it. There is no
  registry; the number is the thread that ties a defect, its fix, and its
  test. R-numbers (`R20`, `R28`) are the older roadmap items the substrate
  packages were built under.

## 8. The eight evidence questions

The technical spine of every evidence deliverable. A record answers each or
marks it out of scope; it never invents a value.

1. Who acted?
2. Under what authority?
3. What context was read?
4. What changed?
5. What could break?
6. What policy applied?
7. What verified it?
8. Who accepted residual risk?

## 9. Where to go next

- Build rules, anti-patterns, and the add-a-command checklist: `AGENTS.md`.
- Command reference: `docs/COMMANDS.md`. Agent CLI contract:
  `docs/agent-cli.md`. MCP tools and presets: `docs/mcp-tools.md`.
- What detectors can and cannot establish: `docs/concepts/detector-evidence.md`
  and `docs/concepts/verification-evidence.md`.
- What leaves the machine: `docs/network-boundary.md`.
- Exit codes and CI: `docs/ci-integration.md`.
- Gateway integrators: `dev/MCP-SECURITY-POSTURE.md`.
- Public copy rules: `docs/website-maintenance.md`, `CONTRIBUTING.md`.
