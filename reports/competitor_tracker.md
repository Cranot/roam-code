# Code Intelligence Market Landscape

> Updated: 24 February 2026
> Sources: GitHub repositories, official documentation, pricing pages, MCP tool registries

---

## Market Overview

The code intelligence space spans several categories: AI IDEs, AI agents, static analysis (SAST), code search, MCP servers, and CLI tools. Each category approaches codebase understanding differently — some focus on editing assistance, others on security scanning, and a growing number on structural analysis via MCP.

---

## MCP Tool Count Comparison

| Tool | MCP Tools | Stars | Local? | Graph Algos? |
|------|-----------|-------|--------|-------------|
| **roam-code** | **99** (23 core) | 286 | Yes | PageRank, Tarjan, Louvain, layers |
| **CKB/CodeMCP** | 76–92 (core 14) | 59 | Yes | Tarjan SCC |
| **Serena MCP** | 40 | 20,500 | Yes | None |
| **SonarQube MCP** | 34+ | 393 | No (Docker+server) | None |
| **CodeGraphContext** | 19 | 775 | Partial (ext DB) | None |
| **CodePrism** | 18 | New | Yes | None |
| **CodeScene MCP** | 14 | 18 | No (API) | None |
| **CodeGraphMCPServer** | 14 | N/A | Yes | Louvain |
| **code-graph-mcp** | 9 | 80 | Yes | Basic centrality |
| **SquirrelSoft** | 8 | 0 | Yes | None |
| **Repomix** | 7 | 22,000 | Yes | None |
| **Code Pathfinder** | 6 | N/A | Yes | None |
| **Claude Context (Zilliz)** | 4 | 5,400 | No (API keys) | None |
| **Greptile v3** | 4 | N/A | No (cloud) | None |
| **ast-grep MCP** | 4 | 338 | Yes | None |
| **CodeQL (community)** | 4 | 134 | Mostly | None |
| **Augment CE** | 1 | N/A | No (cloud) | None |

**Note:** Semgrep ships an MCP Server (beta), but public docs do not currently publish a stable per-tool count.

---

## Feature Comparison Matrix (18 Tools)

| Feature | **roam-code** | Cursor | Windsurf | Augment Code | Claude Code | Codex CLI | Gemini CLI | Aider | Sourcegraph/Amp | SonarQube | CodeQL | Semgrep | ast-grep | Greptile | Repomix | Continue.dev | Serena MCP | CodeScene |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Category** | CLI Tool | AI IDE | AI IDE | AI IDE | AI Agent | AI Agent | AI Agent | AI Agent | Code Search | Code Quality | SAST | SAST | Code Search | Code Intel | Context Pack | IDE Extension | MCP Server | Code Intel |
| **Pricing** | Free | $20-200/mo | $0-60/mo | $20-200/mo | $20-200/mo | $20-200/mo | Free tier | Free (OSS) | Credit-based | Free-Enterprise | Free (OSS) | Free-$40/dev | Free | $30/dev/mo | Free | Free-$10/mo | Free | ~18 EUR/dev/mo |
| **GitHub Stars** | 286 | N/A | N/A | N/A | ~68k | ~61.4k | ~95.3k | ~40.8k | N/A (closed) | ~10.2k | ~9k | ~13k | ~13k | N/A | ~22k | ~31k | ~20.5k | 18 |
| **MCP Tools** | 99 | Client | Client | 1 | Client+Server | Client+Server | Client | 0 native | Server | 34+ | 4 (community) | Server (beta) | 4 | 4 | 7 | 0 (client) | 40 | 14 |
| **Languages** | 26 | 70+ | 70+ | 20+ | 50+ | 50+ | 50+ | ~45 (map) | 30+ | 42+ | 12 | 30+ | 26 | 12 Tier 1 | 19 | 165+ | 30+ | 30+ |
| **Open Source** | Yes (MIT) | No | No | No | No | Yes | Yes | Yes (MIT) | Partial | Partial | Partial | Yes | Yes (MIT) | No | Yes (MIT) | Yes (Apache) | Yes (MIT) | No |
| **100% Local** | Yes | No | No | No | No | No | No | Yes* | No | No** | Yes | Yes | Yes | No | Yes | No | Yes | No |
| **Zero API Keys** | Yes | No | No | No | No | No | No | No | No | No | Yes | Yes | Yes | No | Yes | No | Yes | No |
| **Persistent Index** | Yes (SQLite) | Cloud embed | Cloud embed | Cloud embed | No | No | No | Partial (cache) | Yes (Zoekt) | Per-scan | Yes (QL DB) | No | No | Yes (cloud) | No | Yes (local) | Session | Yes (cloud) |
| **Call Graph** | Yes | No | No | No | No | No | No | No*** | Partial | No | Yes | Partial | No | Yes | No | No | Partial (LSP) | No |
| **PageRank/Centrality** | Yes | No | No | No | No | No | No | Internal | No | No | No | No | No | No | No | No | No | No |
| **Cycle Detection** | Yes (Tarjan) | No | No | No | No | No | No | No | No | Deprecated | No | No | No | No | No | No | No | No |
| **Layer Detection** | Yes | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **Git Churn/Entropy** | Yes | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | Yes |
| **Cognitive Complexity** | Yes | No | No | No | No | No | No | No | No | Yes | No | No | No | No | No | No | No | Yes |
| **Vulnerability Reach** | Yes | No | No | No | No | No | No | No | No | Partial | Yes | Yes | No | No | No | No | No | No |
| **Simulation/What-If** | Yes | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **Multi-Agent Partition** | Yes | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **SARIF Output** | Yes | No | No | No | No | No | No | No | No | Yes | Yes | Yes | Yes | No | No | No | No | No |
| **CI/CD Native** | Yes**** | No | No | Partial | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | No | Yes | No | Yes |
| **CLI Commands** | 134 | N/A | N/A | N/A | N/A | N/A | N/A | ~36 | N/A | 1 | 78 | N/A | 5 | N/A | ~10 | N/A | N/A | N/A |

> \* Aider local with Ollama but needs LLM API key for cloud models
> \** SonarQube MCP requires Docker container + running SonarQube server or Cloud instance
> \*** Aider builds file-level identifier-sharing graph with PageRank internally, NOT function-level call graph, NOT exposed as queryable output
> \**** roam-code CI/CD: GitHub Action, GitLab, Azure, Jenkins, Bitbucket templates

---

## Tool Profiles

### SonarQube MCP Server

Official first-party Docker-based MCP server by SonarSource. 34+ tools across 14+ toolsets. 393 stars, 58 forks, 279 commits. Version 1.10.0.

**Toolset coverage (34+ tools across 14+ toolsets):**
- Analysis (4): `analyze_code_snippet`, `analyze_file_list`, `toggle_automatic_analysis`, `run_advanced_code_analysis`
- Issues (2): `search_sonar_issues_in_projects`, `change_sonar_issue_status`
- Projects (2): `search_my_sonarqube_projects`, `list_pull_requests`
- Quality Gates (2): `get_project_quality_gate_status`, `list_quality_gates`
- Rules (1): `show_rule`
- Duplications (2): `search_duplicated_files`, `get_duplications`
- Measures (2): `get_component_measures`, `search_metrics`
- Security Hotspots (3): `search_security_hotspots`, `show_security_hotspot`, `change_security_hotspot_status`
- Dependency Risks (1): `search_dependency_risks`
- Coverage (2): `search_files_by_coverage`, `get_file_coverage_details`
- Source Code (2): `get_raw_source`, `get_scm_info`
- Languages (1): `list_languages`
- Portfolios (2): `list_portfolios`, `list_enterprises`
- System (5): `get_system_health`, `get_system_info`, `get_system_logs`, `ping_system`, `get_system_status`
- Webhooks (1): `create_webhook`

**Key characteristics:**
- Only `analyze_code_snippet` runs locally (downloads ~200MB analyzers). All other tools query a running SonarQube server.
- 6,500+ rules across 42+ languages (custom parsers, not tree-sitter)
- SonarQube 2026.1 LTA: Rust support, SCA (SBOM generation), AI CodeFix (cloud LLM)
- Architecture analysis deprecated (Java only, removed Jan 2026)

**Strengths:** 6,500+ rules, 42+ languages, Quality Gates, research-backed Cognitive Complexity (SonarSource invented it), SCA/SBOM, mature CI/CD ecosystem.

**Focus:** Code quality gates and issue management. Requires Docker+server infrastructure.

---

### Serena MCP

Coding agent toolkit with 40 documented MCP tools using LSP backends for IDE-grade code navigation. 20,500 stars, 1,400 forks, 117 contributors.

**40 tools across 7 categories:**
- Code Navigation (6): `find_symbol`, `find_referencing_symbols`, `get_symbols_overview` + JetBrains variants
- Code Editing (12): `replace_symbol_body`, `insert_after_symbol`, `rename_symbol`, etc.
- Search (1): `search_for_pattern`
- Memory/Session (5): `write_memory`, `read_memory`, `list_memories`, etc.
- System/Workflow (8): `execute_shell_command`, `restart_language_server`, etc.
- Agent Guidance (6): `initial_instructions`, `think_about_task_adherence`, etc.
- Dashboard (1): `open_dashboard`

**Focus:** Interactive coding agent — helps LLMs navigate and edit code via LSP. Does not produce metrics, graphs, security findings, git analytics, or architectural assessments.

---

### CKB/CodeMCP

Code intelligence platform. Published tool counts vary between documentation pages (76, 80+, 90+, 92 across different pages). 59 stars. Go-based, commercial license.

**Preset system (v7.4 docs):**
| Preset | Tools | Tokens |
|--------|-------|--------|
| core | 14 | ~2,000 |
| review | ~27 | ~3,000 |
| refactor | ~33 | ~4,000 |
| docs | ~27 | ~3,000 |
| ops | ~25 | ~4,000 |
| federation | ~35 | ~4,000 |
| full | 76-80+ | ~10,000 |

**Notable tools:** `searchSymbols`, `getSymbol`, `findReferences`, `traceUsage`, `getCallGraph`, `analyzeImpact`, `compareAPI`, `getAffectedTests`, `getArchitecture`, `findDeadCodeCandidates`, `analyzeCoupling`, `getComplexity`, `getHotspots`, `getOwnership`, `getOwnershipDrift`, `scanSecrets`, `explainSymbol`, `expandToolset`

**v8.1.0 additions:** `findCycles` (Tarjan SCC), `suggestRefactorings`, `planRefactor`, `prepareChange (extract)`, `justify` explanation tool, multi-repo federation, doc-symbol linking.

**Focus:** Code intelligence with a preset-based MCP approach. 10 supported languages.

---

### Aider Repo-Map

Aider uses NetworkX PageRank with a personalization vector on a file-level identifier-sharing graph. ~40.8k stars.

**Technical details:**
- Tree-sitter extracts Tags (name, kind=def|ref, file, line) for ~45 languages
- Builds MultiDiGraph: nodes=files, edges=identifier references across files
- Edge weights: chat mentions x10, long identifiers x10, private ids x0.1, chat files x50
- Runs `nx.pagerank()` with personalization boosting files in current conversation
- Token-budget binary search to fit within `max_map_tokens` (default 1024)
- Three-level caching: disk (persistent), map (in-memory), tree (rendered)

**Key distinction:** Aider's graph is file-level (identifier sharing), not function-level (call relationships). It is not queryable, not persistent across sessions as an index, and not exposed via MCP. PageRank is used to rank file relevance to current chat, not as an architectural metric.

---

### CodeQL

GitHub's SAST tool with Code Property Graph (CFG+DFG). No official MCP server. 78 CLI subcommands. 12 languages.

**Graph capabilities:**

| Aspect | CodeQL | roam-code |
|--------|--------|-----------|
| Graph type | Code Property Graph (AST+CFG+DFG) | Dependency/call graph (NetworkX) |
| Purpose | Security vulnerability detection | Architecture comprehension |
| Call graph | Context-sensitive, cross-file | Tree-sitter extracted |
| Data flow | Local + global, source-to-sink | Intra-procedural baseline |
| Taint tracking | Yes, with flow-state labels | No |
| PageRank/centrality | No | Yes |
| Community detection | No | Yes (Louvain) |
| Topological layers | No | Yes |
| Architecture simulation | No | Yes |
| Git integration | None (point-in-time) | Full (churn, blame, entropy) |

**Focus:** CodeQL answers "is this code exploitable?" — security-first, deep data flow. Different problem domain from architecture comprehension tools.

**Pricing:** Free for OSS. Private repos: $30/committer/month (Code Security) + $19/committer/month (Secret Protection). Requires GitHub Advanced Security.

---

### ast-grep

Rust-based structural code search/rewrite engine. 12,600 stars. 26 built-in languages via tree-sitter. Expressive YAML rule system.

**4 MCP tools (experimental):** `dump_syntax_tree`, `test_match_code_rule`, `find_code`, `find_code_by_rule`

**Strengths:** Deep structural pattern matching with relational operators (`inside`, `has`, `follows`, `precedes`), template-based code rewriting, YAML rule system, Rust performance (sub-second scans). SARIF output (v0.40.0).

**Focus:** Per-invocation structural search and rewrite. No persistent index, graphs, metrics, git history, or architecture analysis.

---

### Repomix

Context packing tool that concatenates files into LLM-friendly format. 22,000 stars. 7 MCP tools. Zero analysis capabilities.

**MCP tools:** `pack_codebase`, `attach_packed_output`, `pack_remote_repository`, `read_repomix_output`, `grep_repomix_output`, `file_system_read_file`, `file_system_read_directory`

**Focus:** Zero-friction file packing for AI consumption. Web interface, browser extension, remote repo support. Demonstrates that simple + focused + zero-friction drives adoption.

---

## Agent Platform Landscape

Major AI coding platforms and their structural code intelligence capabilities:

| Platform | Stars | Built-in Analysis | Notable Limitation |
|----------|-------|-------------------|-------------------|
| **Claude Code** | 68.3k | Glob, Grep, Read, Agent Teams | No persistent index, no call graph |
| **Codex CLI** | 61.4k | Shell commands (rg, find) | No codebase indexing |
| **Gemini CLI** | 95.3k | Codebase Investigator (LLM-driven) | No persistent index (most-requested feature) |
| **Amp** | N/A | Sourcegraph code graph (cloud) | No local graph algorithms, credit-based |
| **Cursor** | N/A | Cloud embeddings, semantic search | No call/dependency graph |
| **Windsurf** | N/A | Cascade, Codemaps (Mermaid) | No deterministic analysis (AI-generated maps) |

**Common pattern:** All platforms have text search; none has built-in structural intelligence (PageRank, Tarjan SCC, Louvain, cognitive complexity, git forensics, architecture simulation, or vulnerability reachability).

---

## Additional MCP Tools Tracked

| Tool | Stars | MCP Tools | Key Feature | Notes |
|------|-------|-----------|-------------|-------|
| **CodePrism** (Rust, MIT) | New | 18 | Graph-based, 1000+ files/sec | AI-generated codebase |
| **code-graph-mcp** | 80 | 9 | PageRank at 4.9M nodes/sec, ast-grep backend | Search-focused |
| **CodeGraphMCPServer** | N/A | 14 | Louvain + GraphRAG | Dormant since Dec 2025 |
| **Code Pathfinder** | N/A | 6 | Call graphs, dependency tracing | Early stage |
| **FalkorDB Code Graph** | N/A | N/A | GraphRAG, Java+Python only | Narrow language support |
| **CodeRLM** | 162 | 0 (REST) | Rust server, 8 languages | Prototype (14 commits) |

---

## AI Code Quality Tools

| Tool | What It Does | MCP? |
|------|-------------|------|
| **OX VibeSec** ($60M B) | Prevents vulns at AI code generation time (10 anti-patterns) | Yes |
| **Codacy AI Risk Hub** | AI governance dashboard, risk scoring, policy enforcement | Yes |
| **TurinTech Artemis** | Evolutionary AI code optimization, Intel partnership | No |
| **GitClear** | Dev analytics dashboard (211M lines study) | No |
| **DeepWiki** (Cognition) | AI-generated wiki/docs/Mermaid for repos | Yes |
| **Qodo 2.1** | Continuous learning rules for AI code review | Unknown |
| **CodeRabbit** | AI PR reviewer, 2M+ repos, pre-configured MCP servers | Client |

---

## Market Events (Feb 2026)

| Event | Context |
|-------|---------|
| **MCP donated to Linux Foundation (AAIF)** | Co-founded by Anthropic, Block, OpenAI — establishes MCP as an industry standard |
| **Official MCP Registry: 518 servers** | Grew from 90 in 1 month |
| **PulseMCP: 8,610+ servers** | Largest MCP directory |
| **41% of MCP servers lack authentication** | Security audit, Feb 21 — local-only tools have an advantage here |
| **SonarQube MCP v1.10.0** | 7 new tools, 3 releases in Feb — active investment in MCP surface |
| **Claude Code: 68.3k stars** | 4% of GitHub commits — largest agent platform by usage |
| **Gemini CLI: 95.3k stars** | 1M token context — persistent indexing is #1 community feature request |
| **Semgrep MCP Server (beta)** | Expanding MCP coverage across SAST tools |
| **CKB v8.1.0** | Added `findCycles` (Tarjan SCC), `suggestRefactorings`, `planRefactor` |
| **grepai: 9 releases in Feb** | 1.3k stars — Go-based semantic search + call graphs; high shipping velocity |
| **Context7 (Upstash): 46.7k stars** | Remote MCP for library docs |
| **Augment CE MCP: GA Feb 6** | 1 tool (`codebase-retrieval`), cloud-only |
| **Continue.dev pivot to CLI (`cn`)** | Open-source CLI for async PR agents |
| **VS Code multi-agent** (v1.110) | Multiple agents in parallel |
| **Cognition acquired Windsurf** | $250M — Devin + Windsurf integration |
| **Sourcegraph → Amp split** | Cody Free discontinued — market consolidation in code search |
| **JetBrains MCP support** (v2025.2) | New distribution channel for MCP tools |
| **GitHub Copilot MCP integration** | Agent mode supports MCP servers |
| **MCP-Atlas benchmark** | Best model 62.3% on multi-tool tasks — compound operations improve success rates |
| **MCP Tool Smells paper** | 97.1% of tools have quality issues — short, targeted descriptions outperform verbose |
| **MCP revision: 2025-11-25** | Current protocol baseline |

---

## Positioning Map

```
                        HIGH Architecture Analysis Depth
                                    |
                                    |
                       CodeScene    |   roam-code
                         (git)      |   (graph+git+AST)
                                    |
                        CKB         |
                        (code intel)|
            SonarQube    CodeQL     |
            (quality)    (security) |
                                    |
    LOW Agent ----------------------+---------------------- HIGH Agent
    Optimization                    |                      Optimization
                                    |
              Semgrep    ast-grep   |   Aider (repo-map)
              (patterns) (patterns) |   Serena (LSP nav)
                                    |   Greptile v3
         Sourcegraph/Amp            |   (graph+LLM)
         (search)                   |   Cursor, Windsurf
                                    |   Augment, Continue
              Repomix               |   Claude Code, Codex CLI
              (packing)             |   Gemini CLI
                                    |
                        LOW Architecture Analysis Depth
```

---

## Category Strengths

Each tool category brings different strengths to the table:

**Architecture & Structure:** roam-code (graph algorithms, simulation, git forensics), CodeScene (git-based analysis), CKB (code intelligence + federation)

**Security & Quality:** SonarQube (6,500+ rules, 42+ languages, Quality Gates), CodeQL (deep dataflow + taint tracking), Semgrep (3,500+ rules, pattern-based)

**Community & Adoption:** Gemini CLI (95.3k stars), Claude Code (68k), Codex CLI (61.4k), Aider (40.8k), Repomix (22k), Serena (20.5k)

**Language Breadth:** SonarQube (42+), Continue.dev/tree-sitter (165+), Serena via LSP (30+)

**Agent Integration:** Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf — all support MCP as clients

---

## MCP Ecosystem Context

- **Current protocol revision:** 2025-11-25
- **Official MCP Registry:** 518 servers (90 → 518 in 1 month)
- **PulseMCP:** 8,610+ servers (largest directory)
- **Total ecosystem:** 17,000+ servers
- **SDK downloads:** 97M+ monthly
- **Governance:** Linux Foundation (AAIF), co-founded by Anthropic + Block + OpenAI
- **Security:** 41% of registry servers lack authentication (Feb 2026 audit)
- **Tool quality:** 97.1% of MCP tool descriptions have quality issues (arXiv:2602.14878)
- **Agent success:** Best model achieves 62.3% on multi-tool tasks (MCP-Atlas)

---

*Data from 200+ public sources, Feb 24 2026*
