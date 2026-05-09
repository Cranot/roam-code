# Installing roam-code

roam-code provides instant codebase comprehension for AI coding agents.
208 commands, 137 MCP tools, 28 languages, 100% local, zero API keys.

## Documentation Hub

- Getting started tutorial: <https://roam-code.com/docs/getting-started>
- Command reference with examples: <https://roam-code.com/docs/command-reference>
- Architecture diagram and subsystem guide: <https://roam-code.com/docs/architecture>

## Quick install

```bash
pip install roam-code
pip install "roam-code[mcp]"  # optional: MCP server support
```

Or with isolated environments:

```bash
pipx install roam-code
# or
uv tool install roam-code
```

## Verify installation

```bash
roam --version
```

## First-time setup (in a project)

```bash
cd /path/to/your/project
roam init        # index + fitness rules + CI workflow
```

This creates `.roam/index.db` (the codebase graph), `.roam/fitness.yaml`
(architectural rules), and `.github/workflows/roam.yml` (CI workflow).

## MCP server setup

### Claude Code
```bash
claude mcp add roam-code -- roam mcp
```

### Claude Desktop / Cursor / VS Code
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

## Requirements

- Python 3.9+
- No native dependencies (pure Python + bundled tree-sitter grammars)
- Works on Linux, macOS, Windows
- No API keys, no accounts, no telemetry

## Key commands for agents

| Command | Purpose |
|---------|---------|
| `roam understand` | Full codebase briefing |
| `roam search <pattern>` | Find symbols by name |
| `roam preflight <symbol>` | Pre-change safety check (blast radius + tests + fitness) |
| `roam context <symbol>` | Files and line ranges to read |
| `roam diff` | Blast radius of uncommitted changes |

Run `roam --help` for all 208 commands (+ alias pairs).
