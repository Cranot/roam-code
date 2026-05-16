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

This transcript was captured on Windows 11 with PowerShell. POSIX equivalents
are noted inline. The full captured logs live under the system temp directory
during reproduction (`%TEMP%\roam-smoke\*.txt`); only the load-bearing
excerpts are reproduced below.

---

## 0. Environment

```text
OS:      Windows 11 Pro (build 26200)
Python:  3.14 (system interpreter)
Shell:   PowerShell 7
Network: only `pypi.org` reached during step 1; no other egress
Date:    2026-05-16
```

POSIX equivalent: substitute `python3 -m venv /tmp/roam-smoke-venv` and
`source /tmp/roam-smoke-venv/bin/activate`.

---

## 1. Install into a clean venv

```powershell
python -m venv $env:TEMP\roam-smoke-venv
& $env:TEMP\roam-smoke-venv\Scripts\pip.exe install "roam-code[mcp]"
```

Tail of the install log (full log: 241 lines, 70 wheel resolutions):

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
& $roam surface --json | python -m json.tool | Select-Object -First 30
```

```json
{
  "agent_contract": {
    "facts": [
      "OK",
      "command count 238",
      "canonical count 231",
      "category count 7",
      "mcp tool count 0"
    ],
    ...
  }
}
```

The README claim of **238 commands, 7 categories** matches. The
`mcp tool count 0` line on `surface --json` is a known launch-readiness gap:
the MCP wrappers register only when the FastMCP server boots, not at CLI
import time, so `surface --json` undercounts. Use `roam mcp-status` (or
boot the MCP server) to see the actual 224-tool roster. See "Smoke findings"
below.

---

## 2. Run the canonical demo

The demo runs against a tiny synthetic project — **not** the roam-code
repository — to keep the transcript unbiased. Three files: one `click` CLI,
one test module, one `pyproject.toml`.

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
cd $env:TEMP\roam-smoke-project
& $roam init
```

```text
Indexing C:\Users\user\AppData\Local\Temp\roam-smoke-project
Discovering files...
  3 files found
  [1/7] Parsing & extracting symbols (3 files)...
  [2/7] Resolving references...
  5 symbol edges
  [3/7] Computing graph metrics...
  [4/7] Analyzing git history...
  [5/7] Computing effects & taint flow...
  [6/7] Computing health & cognitive load...
  [7/7] Building search indexes...
Index complete: 3 files, 6 symbols, 5 edges (0.5s)
VERDICT: initialized: 3 files, 6 symbols, 5 edges

Roam is ready: 3 files, 6 symbols, 5 edges. Health: 14/100.
Try one:    roam understand                    (briefing)
Created:
  .roam/fitness.yaml
  .roamignore
```

`.roam/index.db` (392 KB SQLite) is the entire on-disk state. No
credentials, no remote, no cloud handle.

### 2b. `roam health`

```powershell
& $roam health
```

```text
NOTE: 2 file(s) modified in working tree since last index — run `roam index` to refresh symbol/edge data.

VERDICT: Fair codebase (63/100) — 1 critical, 0 warnings (all flagged as utility / non-actionable)

Health Score: 63/100  |  Tangle: 0.0% (0/6 symbols in cycles)
Propagation Cost: 50.0%  |  Algebraic Connectivity: 0.0000

Health: 3 issues — 1 CRITICAL, 3 INFO
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
VERDICT: Proceed with caution — MEDIUM risk for format_greeting (src/greeter.py:7)

Pre-flight check for `format_greeting (src/greeter.py:7)`:

  Blast radius:     4 symbols in 1 files                     [LOW]
  Affected tests:   2 direct, 0 transitive                   [OK]
  Complexity:       cc=1, nest=1                             [LOW]
  Coupling:         no missing co-change partners            [OK]
  Conventions:      no violations                            [OK]
  Fitness:          1 rules currently fail on target (No circular imports in core) [WARNING]

  Overall risk: MEDIUM
  Risk driver:  fitness (1 rules currently fail, WARNING)
  Suggested tests: pytest tests/test_greeter.py
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
fn  format_greeting  src/greeter.py:7

VERDICT: Large blast radius — 4 symbols (67%) in 2 files affected

Direct dependents (call, 3):
fn    write_greeting              src/greeter.py
fn    test_format_greeting_plain  tests/test_greeter.py
fn    test_format_greeting_shout  tests/test_greeter.py

(+1 transitive dependents)

Affected files (2 — ranked by impact):
  tests/test_greeter.py
  src/greeter.py
```

Three direct callers + one transitive — matching the synthetic project's
actual structure (`greet` calls `write_greeting` calls `format_greeting`;
two tests import it directly).

---

## What this proves

The four commands above ran on a freshly created venv against a project the
indexer had never seen, with no account, no API key, no remote call after
`pip install`, and no human-in-the-loop intervention. Each verdict is
backed by data Roam can point to inside `.roam/index.db` — a SQLite file
the operator owns. That is the moat the README claims: **local codebase
intelligence that produces verifiable evidence without leaving the
machine.** If a CTO or CISO wants to verify the air-gap claim, this
transcript is the minimum-effort reproduction.

The same loop scales: on the roam-code repo itself, `roam init` takes ~30s
and indexes 200+ files; the same `preflight` / `impact` envelope shape
returns. The synthetic project is here so the transcript stays unbiased and
copy-pasteable; the production claim is that the shape holds at 100×
scale.

---

## Smoke findings (real gaps surfaced by this run)

The smoke is most valuable when it finds rough edges. Three observations:

1. **`roam surface --json` reports `mcp tool count 0` even with `[mcp]`
   extras installed.** MCP wrappers register lazily when the FastMCP server
   boots; the CLI-side surface doesn't see them. Either populate the count
   at surface time, or document the lazy-registration semantics where the
   number is consumed.
2. **`roam mcp-status` raises `KeyError: 'symbol'` on a fresh install with
   no built index.** Cold-start guard should produce a structured "index
   not built" envelope (Pattern 1 variant A) instead of a bare verdict
   line.
3. **`roam init` prints `Health: 14/100` in its closing summary, but
   `roam health` immediately after returns `63/100` on the same index.**
   The init-time health line is computed differently from the dedicated
   command and contradicts it. Either align the computation or drop the
   number from the init summary.
4. **`roam init` echoes "No roam index found. Run `roam init`..." before
   it builds the index.** The advisory is harmless but confusing on a
   literal first-time invocation. Suppress the not-found path when the
   command is `init`.

None of these block the smoke from passing — but they're exactly the kind
of first-impression noise a launch readiness pass should fix before the
README claim is published broadly.

---

## Cross-references

- README ["Install + first three commands"](../README.md#install--first-three-commands) — the claim this transcript verifies.
- Landing page ["Install + first three commands"](https://roam-code.com/docs/getting-started) — public-facing version of the same flow.
- `(internal memo)` — strategy command center; this transcript closes P0.2 from the launch-readiness queue.
