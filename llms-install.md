# Installing roam-code

roam-code is the local CLI that runs pre-change gates before every agent edit
and compiles tamper-evident, content-hashed evidence packets after every change.
100% local, no API keys, no telemetry.
<!-- BEGIN auto-count:llms-install-headline -->
276 commands, 244 MCP tools, 28 languages, 100% local, zero API keys.
<!-- END auto-count:llms-install-headline -->

## Cross-references

- `CLAUDE.md` — codebase/agent rules: quality discipline, 12 behavioral laws, adding commands, schema/test discipline. Read first when modifying roam-code itself.
- `AGENTS.md` — sister file to this one for tools using the AGENTS.md convention.
- `README.md` — human-facing overview, install matrix, headline counts.
- Live docs: <https://roam-code.com/docs/getting-started>, <https://roam-code.com/docs/command-reference>, <https://roam-code.com/docs/architecture>.

## Quick install

```bash
pip install "roam-code[mcp]"   # CLI + FastMCP server (recommended for agents)
pip install roam-code          # CLI only
pipx install "roam-code[mcp]"  # isolated env
uv tool install "roam-code[mcp]"
```

Requirements: Python 3.10+. No native deps. Linux / macOS / Windows.

## First-time setup (in a project)

```bash
cd /path/to/your/project
roam init             # index + fitness rules + CI workflow (creates .roam/)
roam health           # 0-100 sanity check
roam understand       # one-screen tour of the codebase
```

`roam init` creates `.roam/index.db` (the codebase graph), `.roam/fitness.yaml`
(architectural rules), and `.github/workflows/roam.yml` (CI workflow).

## MCP server setup

`roam mcp-setup <platform>` is the canonical onramp — it prints (or writes
with `--write`) the exact config block for your tool. Supported platforms:
`claude-code`, `codex-cli`, `cursor`, `gemini-cli`, `vscode`, `windsurf`.

```bash
roam mcp-setup claude-code              # Claude Code CLI
roam mcp-setup cursor --write           # Cursor IDE; writes the file in place
roam mcp-setup vscode --preset full     # VS Code Copilot Agent Mode
roam --json mcp-setup codex-cli         # structured envelope
```

Presets (env var `ROAM_MCP_PRESET`): `core` (57 tools, default, balanced for
daily agent use), `compliance` (13, AI-governance evidence), `review`,
`refactor`, `debug`, `architecture` (task-specific subsets), `full` (227).

Fallback manual config (any MCP client that accepts a JSON command block):

```json
{
  "mcpServers": {
    "roam-code": {
      "command": "roam",
      "args": ["mcp"],
      "env": { "ROAM_MCP_PRESET": "core" }
    }
  }
}
```

After setup, run `roam doctor` to verify the server is registered and reachable.

## Agent-OS modes (declare the action surface)

```bash
roam mode read_only        # read-only analysis
roam mode safe_edit        # edits + finding records allowed
roam mode migration        # schema / data migrations allowed
roam mode autonomous_pr    # stage + commit + open PRs allowed
```

Modes are cumulative: each tier adds capabilities to the previous one. The
MCP boundary enforces the active mode on every tool call (see "MCP runtime
security"); pick the lowest tier that lets the agent finish: `read_only` for
analytics / retrieval copilots, `safe_edit` for AI-assisted PR drafting,
`migration` for schema runs, `autonomous_pr` for hands-off agents. The
active mode rides on every `McpDecisionReceipt` for post-run audits.

## The canonical agent loop (11 steps)

```
 1.  roam runs start                  open run; sets ROAM_RUN_ID (HMAC-chained ledger)
 2.  roam mode safe_edit              declare action surface
 3.  roam pr-bundle init              start proof bundle
 4.  roam preflight <sym>             gate before edit (auto-logs to active run)
 5.  roam impact <sym>                blast radius (auto-logs)
 6.  <edit>
 7.  roam diff | roam critique        review (auto-logs); exit 5 on high severity
 7a. roam findings list               cross-detector findings on the workspace
 8.  roam pr-bundle emit              close bundle with proofs
 9.  roam runs end --with-pr-bundle-emit
10.  roam replay <id>                 narrate the run
11.  roam agent-score                 score the agent on 0..100 composite
```

In CI, `--ci` (or `ROAM_CI=1`) on `pr-bundle emit/validate` implies both
`--strict` and `--strict-resolved` — gates on structural completeness AND on
the absence of unresolved blast-radius symbols.

## Agent-OS substrate (under `.roam/`)

Repo-local state, zero network:

- `.roam/constitution.yml` — unified laws + rules + memory + gates.
- `.roam/runs/` — per-run HMAC-chained event ledger (`roam runs verify`).
- `.roam/leases/` — multi-agent claims (`roam lease claim/release/list`).
- `.roam/memory.jsonl` — portable agent memory (`roam memory ...`).
- `.roam/pr-bundles/` — proof-carrying PR packets (`roam pr-bundle ...`).
- `.roam/laws/` — mined invariants (`roam laws mine/check`).
- `.roam/index.db` — SQLite codebase graph (symbols, edges, findings, git).

## Findings registry

`roam findings list / show / count` queries a normalized cross-detector
registry persisted in the index DB. 28+ detectors emit findings today
(clones, dead, complexity, smells, n1, missing-index, over-fetch,
bus-factor, auth-gaps, vulns, invariants/laws, hotspots, taint, vibe-check,
orphan-imports, conventions, pr-risk, duplicates, audit-trail-conformance,
audit-trail-verify, boundary, test-hermeticity, plus consumer/aggregator
commands). Each row is confidence-tagged: `static_analysis` (deterministic
AST/CFG + taint/dataflow), `structural` (graph/edge patterns), `heuristic`
(name patterns, thresholds, statistical outliers), or `runtime` (requires
ingested traces).

## Evidence compiler (assurance layer)

Every change emits a portable `ChangeEvidence` packet (see
`src/roam/evidence/`) with `schema_version`, content hash, redaction
metadata, and links to the run ledger. From one packet roam projects SARIF,
in-toto/CGA attestations, OSCAL-ish control maps, OTel/GenAI traces,
CycloneDX/VEX, and Markdown reports. Every sensitive MCP tool call also
emits a `McpDecisionReceipt` (`src/roam/evidence/mcp_receipt.py`) — actor
id, tool id, input hash, policy decision, output hash, with redact-on-egress
for secrets, PII, and machine-local paths.

Roam *maps to* and *supports evidence for* compliance controls. It does
not *certify* or *make compliant*.

## MCP runtime security

Every sensitive MCP tool call passes through a wrapper layer that enforces
the active mode, scrubs secrets from the response payload before it reaches
the host LLM context, and emits a signed `McpDecisionReceipt` linked into
the run ledger.

- **Egress redaction.** Secret patterns are scrubbed at the wrapper layer
  before responses return to the host. Lineage is stamped on the receipt
  (`redactions[]` carries reasons from a closed enum: `secret`, `pii`,
  `sensitive_content`, `size_limit`, `policy`, `user_opt_in_required`,
  `machine_local_path`, `schema_strict`, `producer_not_available`).
- **Policy enforcement.** The 4 modes are enforced at the MCP boundary.
  Each receipt records a `policy_decision` from the closed enum `allow` /
  `deny` / `escalate` / `redact` / `not_evaluated` / `would_deny_dry_run`.
- **Receipt integrity.** Receipts are HMAC-linked to ledger events.
  `verify_chain_with_receipts()` returns a `receipt_integrity` value from
  the closed enum `ok` / `missing` / `tampered` / `not_linked`.

The full pattern library (egress regex sources, gateway integration notes,
Interlock / Lasso / Portkey hooks) lives in `dev/MCP-SECURITY-POSTURE.md`.

### Shadow-mode rollout

Roll enforcement out without blocking traffic by setting
`ROAM_MODE_DRY_RUN=1`. The gate evaluates normally, the tool call still
proceeds, and the receipt records `policy_decision: "would_deny_dry_run"`
plus `extra.shadow_mode = true` and `extra.would_deny_reason`. Walk teams
through it:

1. Set `ROAM_MODE_DRY_RUN=1` in the MCP host environment.
2. Run the agent workload for a representative window.
3. Query `roam findings list` (or grep receipts under `.roam/mcp_receipts/`)
   for `would_deny_dry_run` decisions.
4. Tune mode / rules until the unexpected-deny count is zero.
5. Unset `ROAM_MODE_DRY_RUN` to switch to hard enforcement.

### Receipt verification example

After a run completes, a host can verify the receipt chain end-to-end:

```python
from pathlib import Path
from roam.runs.signing import verify_chain_with_receipts, ensure_ledger_key
from roam.runs.ledger import read_run_events

repo_root, run_id = Path.cwd(), "run_2026_05_19_abc123"
events = read_run_events(repo_root, run_id)
key = ensure_ledger_key(repo_root)
result = verify_chain_with_receipts(events, key, repo_root, run_id)
# result["receipt_integrity"] -> closed enum:
#   "ok"         every receipt-bearing event resolves to an on-disk receipt
#                whose sha256 matches the chain-baked hash
#   "missing"    a receipt was deleted after signing
#   "tampered"   a receipt was edited after signing
#   "not_linked" no sensitive tool fired a receipt this run (advisory)
```

### Schema export & validation

The `McpDecisionReceipt` dataclass lives at
`src/roam/evidence/mcp_receipt.py`. Gateways pin the receipt shape via the
JSON Schema Draft 2020-12 emitter:

```bash
python scripts/export_mcp_receipt_schema.py > mcp-receipt.schema.json
```

Closed-enum vocabulary is pulled by reference at build time, so a
vocabulary edit propagates without a schema re-roll.

## World-model classifiers (per-symbol semantic facts)

| Command | Reports |
|---------|---------|
| `roam side-effects ensure_index` | `io_read`, `io_write`, `mutation`, `process`, or `none` |
| `roam idempotency ensure_index` | `idempotent`, `non_idempotent`, or `unknown` |
| `roam causal-graph ensure_index` | Param-to-sink dependency edges |
| `roam tx-boundaries` | Begin/commit/rollback regions and unsafe mutations outside transactions |

## Key commands for AI assistants

| Command | Purpose |
|---------|---------|
| `roam understand` | One-screen codebase briefing |
| `roam search <pattern>` | Find symbols by name |
| `roam retrieve "<task>"` | Graph-aware FTS5 + structural rerank for free-form tasks |
| `roam context <symbol>` | Exact files + line ranges to read before changing |
| `roam preflight <symbol>` | Blast radius + tests + fitness gate |
| `roam impact <symbol>` | What breaks if this symbol changes |
| `roam diagnose <symbol>` | Root-cause ranking for failing behaviour |
| `roam diff` | Blast radius of uncommitted changes |
| `git diff \| roam critique` | Patch verifier (clones-not-edited, blast radius; exit 5 on high severity) |
| `roam adversarial` | Architectural challenges on changed files (composes cycles + clusters + layers + catalog + dead + complexity) |
| `roam grep <pattern>` | Index-aware text search (reachability, PageRank, clones, bridges) |
| `roam refs-text <string>` | String audit: SAFE-TO-REMOVE / REVIEW / LOAD-BEARING |

Run `roam --help` for the 5-verb core, `roam --help-all` for every command,
and `roam surface --json` for the machine-readable inventory.

## Output formats

- `roam --json <cmd>` — structured envelope (default for MCP), schema-versioned.
- `roam --sarif <cmd>` — SARIF 2.1.0 for GitHub Code Scanning / CI (health, debt, complexity, smells, vulns, …).

## Further reading

- `dev/MCP-SECURITY-POSTURE.md` — gateway-integrator companion: egress
  redaction pattern library, mode-gate enforcement, receipt-chain
  invariants, Interlock / Lasso / Portkey hooks.
- `src/roam/evidence/mcp_receipt.py` — canonical `McpDecisionReceipt` shape.
- `scripts/export_mcp_receipt_schema.py` — Draft 2020-12 schema emitter.
- MCP runtime security design discussion:
  <https://github.com/Cranot/roam-code/discussions/37#discussioncomment-16967163>

<!-- BEGIN auto-count:llms-install-footer -->
Run `roam --help` for all 276 commands (+ alias pairs).
<!-- END auto-count:llms-install-footer -->
