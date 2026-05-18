# Installing roam-code

roam-code is the local CLI that runs pre-change gates before every agent edit and compiles post-change evidence packets that hash-verify offline.
<!-- BEGIN auto-count:llms-install-headline -->
241 commands, 227 MCP tools, 28 languages, 100% local, zero API keys.
<!-- END auto-count:llms-install-headline -->

## Documentation Hub

- Getting started tutorial: <https://roam-code.com/docs/getting-started>
- Command reference with examples: <https://roam-code.com/docs/command-reference>
- Architecture diagram and subsystem guide: <https://roam-code.com/docs/architecture>

## Quick install

```bash
pip install "roam-code[mcp]"   # CLI + FastMCP server (recommended for agents)
# or, CLI only (no MCP wrapper):
pip install roam-code
```

Or with isolated environments:

```bash
pipx install "roam-code[mcp]"
# or
uv tool install "roam-code[mcp]"
```

## Verify installation

```bash
roam --version
```

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

The MCP server is the entry point `roam mcp` (requires the `[mcp]` extra).
It exposes **57 tools in the core preset** and up to **224 in the `full` preset**.

### Claude Code
```bash
claude mcp add roam-code -- roam mcp
```

### Claude Desktop / Cursor / Cline / VS Code
Add to your MCP config:

```json
{
  "mcpServers": {
    "roam-code": {
      "command": "roam",
      "args": ["mcp"]
    }
  }
}
```

To enable the full 224-tool preset, set `ROAM_MCP_PRESET=full` in the server env.

## Requirements

- Python 3.10+
- No native dependencies (pure Python + bundled tree-sitter grammars)
- Works on Linux, macOS, Windows
- No API keys, no accounts, no telemetry

## Agent-OS modes (declare the action surface)

```bash
roam mode read_only         # read-only analysis
roam mode safe_edit         # edits + finding records allowed
roam mode migration         # schema / data migrations allowed
roam mode autonomous_pr     # stage + commit + open PRs allowed
```

Modes are cumulative: each adds capabilities to the previous tier.

## The agent loop (canonical 11-step workflow)

```
1.  roam runs start                       open run, get ROAM_RUN_ID (HMAC-signed events)
2.  roam mode safe_edit                   declare action surface
3.  roam pr-bundle init                   start proof bundle
4.  roam preflight <sym>                  gate before edit (auto-logs to active run)
5.  roam impact <sym>                     blast radius (auto-logs)
6.  <edit>
7.  roam diff | roam critique             review (auto-logs)
7a. roam findings list                    cross-detector findings on the workspace
8.  roam pr-bundle emit                   close bundle with proofs
9.  roam runs end --with-pr-bundle-emit
10. roam replay <id>                      narrate the run
11. roam agent-score                      score the agent on 0..100 composite
```

## Findings registry

`roam findings list / show / count` queries a cross-detector registry persisted in
the index DB. Detectors that emit findings today include `clones`, `dead`,
`complexity`, with `smells`, `n1`, and `missing-index` in flight. Each row is
confidence-tagged (`static_analysis`, `structural`, `heuristic`) for triage.

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

## World-model classifiers (per-symbol semantic facts)

```
roam side-effects     io_read / io_write / mutation / process / none, per symbol
roam idempotency      idempotent / non_idempotent / unknown
roam causal-graph     param -> sink dependency edges per symbol
roam tx-boundaries    begin/commit/rollback regions; flags unsafe_mutation outside tx
```

## Output formats

- `roam --json <cmd>` — structured envelope (default for MCP), schema-versioned
- `roam --sarif health` — SARIF 2.1.0 for GitHub Code Scanning / CI

<!-- BEGIN auto-count:llms-install-footer -->
Run `roam --help` for all 241 commands (+ alias pairs).
<!-- END auto-count:llms-install-footer -->
