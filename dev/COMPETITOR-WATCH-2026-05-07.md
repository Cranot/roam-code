# Competitor watch — 2026-05-07

Research conducted during the autonomous overnight run. Sources: WebSearch.

## NEW DIRECT COMPETITORS (code-graph-MCP space)

### GitNexus — 10K+ GitHub stars, early 2026
- Open-source code-graph + MCP server, builds KuzuDB graph database
- WebAssembly variant (tree-sitter WASM + KuzuDB WASM + transformers.js)
- Connects to Claude Code, Cursor, Windsurf
- **Direct competitor.** 20× the GitHub stars Roam has.
- Differentiation gap: Roam's 27 dedicated extractors > their generic tree-sitter, Roam's bridges + EU AI Act audit trail are unique
- Source: paperclipped.de blog

### Codebase-Memory — late March 2026, arxiv paper
- 66 languages (vs Roam 27)
- Tree-sitter knowledge graph via MCP
- Parallel worker pools (Roam currently sequential — see R13)
- Claims 83% answer quality at 10× fewer tokens vs file-exploration agent
- Source: arxiv.org/abs/2603.27277

### codesight-mcp — security-hardened
- 34 MCP tools, byte-offset precision
- "99% token cost reduction" claim
- Source: github.com/cmillstead/codesight-mcp

### CodeGraphContext, code-graph-mcp, code-review-graph — smaller competitors
- Multiple MCP-server-for-code-graph projects appearing in 2026
- Most are 5K-10K LoC, single-language or limited tier coverage

## MAJOR PLAYER MOVES

### Sourcegraph Cody MCP server (April 2026)
- Sourcegraph released their own MCP server
- Cody's agentic context-gathering supports MCP tools
- Integrates with Claude Code, Cursor, Amp
- "Smart hover summaries" use precise code intelligence
- Source: sourcegraph.com/mcp + sourcegraph.com/changelog

### Qodo 2.0 (Feb 2026)
- Multi-agent architecture: separate agents for bugs / security / quality / test coverage
- Highest F1 score (60.1%) among 8 tested tools
- Added cross-repo dependency tracking (Enterprise tier)
- v2.1: Rule System (beta) — centralized framework for engineering standards
- **Implication for Roam**: rule packs should become more central; multi-agent positioning available

### Greptile v4 (March 2026)
- Claims 82% bug catch rate (vs Qodo 60.1% F1, CodeRabbit ~44%)
- **Implication for Roam**: publish bench numbers more prominently; the eval/ harness already shows recall@20 = 0.539

### CodeRabbit Autofix (April 2026, early access)
- Agent-based fixing, won't auto-merge
- **Implication for Roam**: Roam Review's "suggested fix" should evolve toward real autofix

## STRATEGIC CONCLUSIONS

1. **The MCP-server-for-code-graph space is now crowded.** 6+ direct competitors in last 60 days.
2. **GitNexus's 10K stars** is a serious adoption signal we need to match. Their WebAssembly play is interesting — Roam-in-the-browser is feasible with sqlite-wasm + tree-sitter-wasm.
3. **Codebase-Memory's 66 languages** outguns Roam's 27. Add Dart (R4) bumps to 28; add R, Lua, Bash, Solidity, Zig over time to push past 35.
4. **Qodo's Rule System** validates Roam's existing 2,489+ community rules. Should make this more visible on the website.
5. **Greptile's published 82%** — Roam should publish its bench numbers. Add to website + release notes.
6. **Sourcegraph legitimizing MCP-for-code** is a tailwind, not a threat — they raise the category awareness Roam needs.

## ACTIONS DERIVED

- R4 (Dart): keep, urgent — must compete on language count
- R7 (SARIF enrichment): differentiator vs competitors
- R9, R10 (Rust + Swift rule packs): doubles down on the rule-pack moat
- R13 (parallel parse): Codebase-Memory has it, Roam needs it
- R14 (LLM-explain MCP tool): catches up to Cody's "smart hover summaries"
- R20 (release v12.43): include published bench numbers in release notes
- Future (post-tonight): consider WebAssembly variant of Roam (compete with GitNexus)
- Future: consider multi-agent positioning for Roam Review (mirror Qodo)
- Future: real autofix in Review (catch up to CodeRabbit)

## SOURCES (verified 2026-05-07)
- Codebase-Memory paper: https://arxiv.org/abs/2603.27277
- GitNexus blog post: https://www.paperclipped.de/en/blog/gitnexus-code-knowledge-graph-ai-agents
- Sourcegraph MCP: https://sourcegraph.com/mcp
- Qodo 2.0 review: https://dev.to/rahulxsingh/qodo-vs-coderabbit-ai-code-review-tools-compared-2026-kdp
- CodeRabbit Autofix: noted in dev.to comparison
- code-graph-mcp: https://github.com/sdsrss/code-graph-mcp
- codesight-mcp: https://github.com/cmillstead/codesight-mcp
- code-review-graph: https://github.com/tirth8205/code-review-graph
