## Summary

Brief description of what this PR does and why.

## Changes

-

## Testing

- [ ] All tests pass (`pytest tests/`)
- [ ] New tests added for new functionality
- [ ] Existing tests updated if behavior changed

## Checklist

- [ ] `from __future__ import annotations` at top of new files
- [ ] Code follows project conventions (snake_case functions, PascalCase classes, plain ASCII output)
- [ ] JSON output uses `json_envelope()` with `verdict` in summary (if applicable)
- [ ] New command registered in `cli.py` `_COMMANDS` and `_CATEGORIES` (if applicable)
- [ ] MCP tool added in `mcp_server.py` (if new command is useful for agents)
