# Fresh-install smoke transcript

A clean-environment install and end-to-end run on a three-file synthetic
project, captured verbatim. Anyone with Python 3.10+ and `pip` can replay
these commands and reproduce the output. The transcript proves three claims
from the README's "Install + first three commands" section without taking
Roam's word for it:

1. **Zero accounts, zero API keys, zero cloud logins.** Install is one
   `pip install`; nothing else is configured before commands run.
2. **Zero network egress after `pip install`.** Indexing, health, preflight,
   and impact all read and write a local SQLite DB under `.roam/`. No HTTP
   client is exercised in the four demo commands.
3. **The "first three commands" actually do what they claim.** A new repo
   reaches a working `preflight` verdict in under a minute on a synthetic
   project, with real blast-radius, tests-affected, complexity, and fitness
   data — not a stubbed envelope.

Captured on Windows 11 with PowerShell; POSIX equivalents noted inline. Full
logs live under `%TEMP%\roam-smoke-fresh\*.txt` during reproduction; only
load-bearing excerpts are reproduced below.

---

## 0. Environment

```text
OS: Windows 11 Pro (build 26200) | Python: 3.14 | Shell: PowerShell 7
Network: only pypi.org reached during step 1; no other egress
Date: 2026-05-18
```

POSIX equivalent: substitute `python3 -m venv /tmp/roam-smoke-venv` and
`source /tmp/roam-smoke-venv/bin/activate`.

---

## 1. Install into a clean venv

```powershell
python -m venv $env:TEMP\roam-smoke-venv
& $env:TEMP\roam-smoke-venv\Scripts\pip.exe install "roam-code[mcp]"
```

Tail of the install log (full log: ~241 lines, ~70 wheel resolutions):

```text
Downloading roam_code-13.2-py3-none-any.whl (3.1 MB)
...
Successfully installed aiofile-3.11.1 ... roam-code-13.2 ... fastmcp-3.3.1
```

Verify the binary and version:

```powershell
$roam = "$env:TEMP\roam-smoke-venv\Scripts\roam.exe"
& $roam --version
```

```text
roam, version 13.2
```

`roam surface --json` is the canonical machine-readable inventory. The
top-level envelope reports its counts in `agent_contract.facts` (the JSON
also exposes `commands[]`, `categories[]`, and `mcp_tools[]` arrays):

```powershell
& $roam surface --json | python -m json.tool | Select-Object -First 16
```

```json
{
  "_meta": { "cacheable": true, "cache_ttl_s": 300, "..." : "..." },
  "agent_contract": {
    "facts": [
      "OK",
      "command count 241",
      "canonical count 234",
      "category count 7",
      "mcp tool count 227"
    ]
  }
}
```

The counts above are the captured v13.2 smoke output, not a hand-maintained
headline. For the current count, run `roam surface --json` in the checkout
or `python dev/build_readme_counts.py --check`. The
`summary.mcp_tool_count_by_preset` field on the same envelope breaks the
MCP-tool total down per preset (`core: 57`, `review: 70`, `refactor: 70`,
`debug: 69`, `architecture: 71`, `compliance: 13`, `full: 227`).

---

## 2. Run the canonical demo

The demo runs against a tiny synthetic project — **not** the roam-code
repository — to keep the transcript unbiased. Three source files plus one
`pyproject.toml`: a `click` CLI, a test module, and a project manifest.

```text
greeter-smoke/
  pyproject.toml
  src/greeter.py          # format_greeting, write_greeting, greet (click)
  tests/test_greeter.py   # CliRunner tests covering format_greeting
```

After `git init && git add -A && git commit -m initial`, the four canonical
commands:

### 2a. `roam init`

```powershell
cd $env:TEMP\roam-smoke-fresh
& $roam init
```

```text
No index found. Building...
Indexing C:\Users\user\AppData\Local\Temp\roam-smoke-fresh
Discovering files...
  4 files found
  [1/7] Parsing & extracting symbols (4 files)...
  [2/7] Resolving references...
  5 symbol edges
  [3/7] Computing graph metrics...
  [4/7] Analyzing git history...
  [5/7] Computing effects & taint flow...
  [6/7] Computing health & cognitive load...
  [7/7] Building search indexes...
Index complete: 4 files, 5 symbols, 5 edges (0.5s)
VERDICT: initialized: 4 files, 5 symbols, 5 edges

Roam is ready: 4 files, 5 symbols, 5 edges.

Try one:    roam health                        (score this codebase 0-100)
            roam understand                    (briefing)
Created:
  .roam/fitness.yaml
  .roamignore
```

`.roam/index.db` (~392 KB SQLite) is the entire on-disk state. No
credentials, no remote, no cloud handle.

### 2b. `roam health`

```powershell
& $roam health
```

```text
VERDICT: Fair codebase (63/100) — 1 critical, 0 warnings (all flagged as utility / non-actionable)

Health Score: 63/100  |  Tangle: 0.0% (0/5 symbols in cycles)
Propagation Cost: 75.0%  |  Algebraic Connectivity: 0.0000

Health: 3 issues — 1 CRITICAL, 3 INFO
  (0 actionable cycles, 1 local/test cycle ignored, 3 bottlenecks (3 actionable, 0 expected utilities))
  Breakdown: cycles [0 issues], god [0 issues], bottlenecks [1 CRITICAL, 2 INFO], layers [0 issues]

Top CRITICAL issues (run `roam --detail health` for the full breakdown):
  bottleneck: format_greeting (fn, betweenness=5.0)
```

`format_greeting` is genuinely the betweenness bottleneck on this graph —
it sits between `greet`/`write_greeting` and the two tests. That makes it
the right symbol to probe with `preflight` and `impact`.

### 2c. `roam preflight format_greeting`

```powershell
& $roam preflight format_greeting
```

```text
VERDICT: Proceed with caution — MEDIUM risk for format_greeting (src/greeter.py:8)

Pre-flight check for `format_greeting (src/greeter.py:8)`:

  Blast radius:     4 symbols in 1 files                     [LOW]
  Affected tests:   2 direct, 0 transitive                   [OK]
  Complexity:       cc=1, nest=1                             [LOW]
  Coupling:         no missing co-change partners            [OK]
  Conventions:      no violations                            [OK]
  Fitness:          1 rules currently fail on target (No circular imports in core) [WARNING]

  Overall risk: MEDIUM
  Risk driver:  fitness (1 rules currently fail, WARNING)
  Suggested tests: pytest tests/test_greeter.py

NEXT STEPS:
  1. Run `roam affected-tests format_greeting` to know which test suite covers your change
```

The 5-signal envelope (blast / tests / complexity / coupling / conventions /
fitness) is populated from real graph data. The "Suggested tests" line names
the actual test file added by the synthetic project — proof the
`affected-tests` substrate found it.

### 2d. `roam impact format_greeting`

```powershell
& $roam impact format_greeting
```

```text
fn  format_greeting  src/greeter.py:8

VERDICT: Large blast radius — 4 symbols (80.0%) in 2 files affected (risk_level high)

Affected symbols: 4  Affected files: 2

Direct dependents (call, 3):
kind  name                        file
----  --------------------------  ---------------------
fn    write_greeting              src/greeter.py
fn    test_format_greeting_plain  tests/test_greeter.py
fn    test_format_greeting_shout  tests/test_greeter.py

(+1 transitive dependents)

Affected files (2 — ranked by impact):
  src/greeter.py
  tests/test_greeter.py

NEXT STEPS:
  1. Run `roam affected-tests format_greeting` to find tests that exercise the impacted surface
  2. Run `roam preflight format_greeting` for a one-shot risk verdict combining all signals
```

Three direct callers + one transitive — matching the synthetic project's
actual structure (`greet` calls `write_greeting` calls `format_greeting`;
two tests import it directly).

---

## 3. Teardown

The smoke run lives entirely under the system temp directory. Cleanup is
two `Remove-Item` calls (POSIX: `rm -rf`):

```powershell
Remove-Item -Recurse -Force $env:TEMP\roam-smoke-fresh, $env:TEMP\roam-smoke-venv
```

No system-wide state was touched. `pip install` only writes into the venv;
`roam init` only writes `.roam/` inside the synthetic project; no PATH,
registry, or user config is modified.

---

## What this proves

The four commands above ran on a freshly created venv against a project the
indexer had never seen, with no account, no API key, no remote call after
`pip install`, and no human-in-the-loop intervention. Each verdict is
backed by data Roam can point to inside `.roam/index.db` — a SQLite file
the operator owns. That is the moat the README claims: **local codebase
intelligence that produces verifiable evidence without leaving the machine.**
If a CTO or CISO wants to verify the air-gap claim, this transcript is the
minimum-effort reproduction. The same loop scales to larger repositories;
the `preflight` / `impact` envelope shape is identical.

---

## Smoke findings (real gaps surfaced and closed)

The smoke is most valuable when it finds rough edges. The original run
surfaced four first-run issues; all four are closed in code and pinned by
regression tests or generated-count checks:

1. **`roam surface --json` reports `mcp tool count 0` even with `[mcp]`
   extras installed.** *RESOLVED (W1290).* The CLI-side surface reads the
   MCP tool count via AST scan of `src/roam/mcp_server.py`, env-independent
   and resilient to optional-extras import errors. Fresh installs now
   report `mcp tool count 227`.
2. **`roam mcp-status` raises `KeyError: 'symbol'` on a fresh install with
   no built index.** *RESOLVED (W1289).* The command now emits a structured
   Pattern-1A prerequisite envelope instead of a traceback.
3. **`roam init` prints `Health: 14/100` then `roam health` returns
   `63/100`.** *RESOLVED (W1288).* The init banner no longer prints a quick
   health score; users are pointed at `roam health` for the canonical
   computation.
4. **`roam init` echoes "No roam index found. Run `roam init`..." before
   building the index.** *RESOLVED (W1291).* `cmd_init` suppresses the
   self-recommendation; other cold-start commands keep the advisory.

Keep this section in the transcript: it documents why fresh-install smoke is
not just marketing proof but a launch-readiness harness that catches
first-impression defects.

---

## Cross-references

- README ["Install + first three commands"](../README.md#install--first-three-commands) — the claim this transcript verifies.
- Landing page ["Install + first three commands"](https://roam-code.com/docs/getting-started) — public-facing version of the same flow.
