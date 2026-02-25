# roam-code Competitor Tracker

> Updated: 24 February 2026 (Round 6 — deep competitive gap analysis + fresh web verification)
> Sources: 16 Opus research agents (10 prior + 6 deep-dive), 200+ web sources, plus Round 5 primary-doc recheck
> Method: Source code review, GitHub repos, docs, pricing, MCP tool enumeration
> Detailed per-competitor reports: `reports/competitors/`
> Public web artifact: `docs/site/index.html` (deployed by `.github/workflows/pages.yml`)
> Site data generator: `src/roam/competitor_site_data.py` → `docs/site/data/landscape.json`

---

## Headline Finding

**roam-code has a massive capability lead over every competitor in the code intelligence space.**

No competitor combines: graph algorithms (PageRank, Tarjan SCC, Louvain), git archaeology, architecture analysis/simulation, vulnerability reachability, multi-agent partitioning, and SARIF output — all in a single local tool with zero API keys.

**One competitor may still have more MCP tools (CKB docs now conflict: 76/80+ vs 90+/92), but still lacks graph algorithms and architecture simulation.**

---

## Delivery Decision Snapshot (Have / Must / Cannot / Limitations)

This section is the execution bridge from competitor inventory to product decisions.

### A) What We Have (Shipped)

| Capability | Status |
|-----------|--------|
| Broad analytical surface | **134 canonical CLI commands (+1 alias)** shipped |
| MCP agent interface | **99 tools (23 core)** shipped |
| Graph intelligence | PageRank + Tarjan SCC + Louvain + layers shipped |
| CI + SARIF flow | GitHub Action + SARIF + sticky PR comments shipped |
| Agent context optimization | Presets, compounds, `--budget` partial, deterministic output shipped |
| Governance stack | `vibe-check`, `ai-readiness`, `verify`, `duplicates`, ownership suite shipped |

### B) What We Must Implement Before v11 (Unless Blocked/Impossible)

| Item | Why |
|------|-----|
| `#93` Structural rule packs + optional autofix templates | Competitively closes ast-grep-like policy workflows |
| `#95` Daemon/webhook warm-index path | Faster CI/PR cycles on large repos |
| `#96` Pre-indexed framework/library packs | Better cold-start context quality |
| `#98` Streamable HTTP transport + auth compatibility | Protocol alignment and client interoperability |
| `#99` Full MCP annotations + `taskSupport` | Better planner/tool behavior across clients |
| `#100` Cross-client conformance suite | Prevents regressions across Copilot/Claude/Codex/Gemini |

### C) What We Should Not Integrate (Core Product)

| Pattern | Decision |
|--------|----------|
| Cloud-required analysis + mandatory API keys | Do not integrate in core |
| Default arbitrary shell/python execution MCP tools | Do not integrate in core |
| Black-box AI auto-fix as primary analysis mode | Do not set as default |
| SaaS-only governance dashboard as core surface | Out of scope for core CLI/MCP engine |

### D) Current Limitations (Known and Tracked)

| Limitation | Tracking |
|-----------|----------|
| CI coverage beyond GitHub Actions | Backlog follow-up (GitLab/Azure/Jenkins expansion) |
| No neural embedding search in core | `#54` planned |
| No IDE plugin surface | Deliberately CLI/MCP-first; plugin work is follow-up |
| MCP 2025-11-25 transport/auth modernization gap | `#98`, `#100` |

---

## MCP Tool Count Leaderboard

| Tool | MCP Tools | Stars | Local? | Graph Algos? |
|------|-----------|-------|--------|-------------|
| **CKB/CodeMCP** | **76/80+ vs 90+/92** (core 14) | 59 | Yes | None |
| **roam-code** | **99** (23 core) | 286 | **Yes** | **PageRank, Tarjan, Louvain, layers** |
| **Serena MCP** | **40** (documented list) | 20,500 | Yes | None |
| **SonarQube MCP** | **34+** | 391 | No (Docker+server) | None |
| **CodeGraphContext** | **19** | 775 | Partial (ext DB) | None |
| **CodePrism** | **18** | New | Yes | None |
| **CodeScene MCP** | **14** | 18 | No (API) | None |
| **CodeGraphMCPServer** | **14** | N/A | Yes | **Louvain** |
| **code-graph-mcp** | **9** | 80 | Yes | Basic centrality |
| **SquirrelSoft** | **8** | 0 | Yes | None |
| **Code Pathfinder** | **6** | N/A | Yes | None |
| **Repomix** | **7** | 22,000 | Yes | None |
| **Claude Context (Zilliz)** | **4** | 5,400 | No (API keys) | None |
| **Greptile v3** | **4** | N/A | No (cloud) | None |
| **ast-grep MCP** | **4** | 338 | Yes | None |
| **CodeQL (community)** | **4** | 134 | Mostly | None |
| **Augment CE** | **1** | N/A | No (cloud) | None |

**Note:** Semgrep now ships an MCP Server (beta), but public docs do not currently publish a stable per-tool count; it is tracked in the matrix as `Server (beta)` until a count is documented.

---

## Master Comparison Table (18 Competitors)

| Feature | **roam-code** | Cursor | Windsurf | Augment Code | Claude Code | Codex CLI | Gemini CLI | Aider | Sourcegraph/Amp | SonarQube | CodeQL | Semgrep | ast-grep | Greptile | Repomix | Continue.dev | Serena MCP | CodeScene |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Category** | CLI Tool | AI IDE | AI IDE | AI IDE | AI Agent | AI Agent | AI Agent | AI Agent | Code Search | Code Quality | SAST | SAST | Code Search | Code Intel | Context Pack | IDE Extension | MCP Server | Code Intel |
| **Pricing** | Free | $20-200/mo | $0-60/mo | $20-200/mo | $20-200/mo | $20-200/mo | Free tier | Free (OSS) | Credit-based | Free-Enterprise | Free (OSS) | Free-$40/dev | Free | $30/dev/mo | Free | Free-$10/mo | Free | ~18 EUR/dev/mo |
| **GitHub Stars** | 286 | N/A | N/A | N/A | ~68k | ~61.4k | ~95.3k | ~40.8k | N/A (closed) | ~10.2k | ~9k | ~13k | ~13k | N/A | ~22k | ~31k | ~20.5k | 18 |
| **MCP Tools** | **99** | Client | Client | 1 | Client+Server | Client+Server | Client | 0 native | Server | **34+** | 4 (community) | Server (beta) | 4 | 4 | 7 | 0 (client) | **40** | 14 |
| **Languages** | 26 | 70+ | 70+ | 20+ | 50+ | 50+ | 50+ | ~45 (map) | 30+ | **42+** | 12 | 30+ | 26 | 12 Tier 1 | 19 | 165+ | **30+** | 30+ |
| **Open Source** | Yes (MIT) | No | No | No | No | Yes | Yes | Yes (MIT) | Partial | Partial | Partial | Yes | Yes (MIT) | No | Yes (MIT) | Yes (Apache) | Yes (MIT) | No |
| **100% Local** | **Yes** | No | No | No | No | No | No | Yes* | No | No** | **Yes** | **Yes** | **Yes** | No | **Yes** | No | **Yes** | No |
| **Zero API Keys** | **Yes** | No | No | No | No | No | No | No | No | No | Yes | Yes | Yes | No | Yes | No | Yes | No |
| **Persistent Index** | **Yes (SQLite)** | Cloud embed | Cloud embed | Cloud embed | No | No | No | Partial (cache) | Yes (Zoekt) | Per-scan | Yes (QL DB) | No | No | Yes (cloud) | No | Yes (local) | Session | Yes (cloud) |
| **Call Graph** | **Yes** | No | No | No | No | No | No | No*** | Partial | No | **Yes** | Partial | No | Yes | No | No | Partial (LSP) | No |
| **PageRank/Centrality** | **Yes** | No | No | No | No | No | No | Internal | No | No | No | No | No | No | No | No | No | No |
| **Cycle Detection** | **Yes (Tarjan)** | No | No | No | No | No | No | No | No | Deprecated | No | No | No | No | No | No | No | No |
| **Layer Detection** | **Yes** | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **Git Churn/Entropy** | **Yes** | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | **Yes** |
| **Cognitive Complexity** | **Yes** | No | No | No | No | No | No | No | No | **Yes** | No | No | No | No | No | No | No | **Yes** |
| **Vulnerability Reach** | **Yes** | No | No | No | No | No | No | No | No | Partial | **Yes** | **Yes** | No | No | No | No | No | No |
| **Simulation/What-If** | **Yes** | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **Multi-Agent Partition** | **Yes** | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No | No |
| **SARIF Output** | **Yes** | No | No | No | No | No | No | No | No | Yes | **Yes** | **Yes** | **Yes** | No | No | No | No | No |
| **CI/CD Native** | **Yes**** | No | No | Partial | Yes | Yes | Yes | Yes | Yes | **Yes** | **Yes** | **Yes** | Yes | Yes | No | Yes | No | **Yes** |
| **CLI Commands** | **134 canonical (+1 alias)** | N/A | N/A | N/A | N/A | N/A | N/A | ~36 | N/A | 1 | 78 | N/A | 5 | N/A | ~10 | N/A | N/A | N/A |

> \* Aider local with Ollama but needs LLM API key for cloud models
> \** SonarQube MCP requires Docker container + running SonarQube server or Cloud instance
> \*** Aider builds file-level identifier-sharing graph with PageRank internally, NOT function-level call graph, NOT exposed as queryable output
> \**** roam-code CI/CD: GitHub Action is shipped; current gap is cross-platform CI coverage beyond GitHub

## Matrix Recheck Log (Round 6 - 24 Feb 2026)

| Competitor | Rechecked Claim | Previous Matrix | Round 5 Finding | Confidence | Source |
|------------|-----------------|-----------------|-----------------|------------|--------|
| roam-code | Internal surface counts | `65 MCP`, `96 CLI` in prior tracker snapshot | Source-of-truth count is `99 MCP tools (23 core)` and `134 canonical CLI commands (+1 alias)`; matrix rows updated accordingly. | High | `src/roam/surface_counts.py` + `tests/test_surface_counts.py` |
| CKB/CodeMCP | MCP tool count | `~92 (21 core)` | Published counts are inconsistent: docs show `76` and `80+` full-preset references (core `14`), while other CKB pages still state `90+` or `92`. Matrix now tracks a range. | High | https://docs.codeknowledge.dev/mcp/getting-started/mcp-integration ; https://docs.codeknowledge.dev/news/token-economics-of-mcp-toolsets ; https://docs.codeknowledge.dev/mcp/getting-started/mcp-tools-reference ; https://www.codeknowledge.dev/pricing |
| Serena MCP | MCP tool count | `~45` | Serena docs enumerate 40 tools in the tools list page. | High | https://oraios.github.io/serena/docs/mcp_guide/available_tools/ |
| Repomix | MCP tool count | `6` | Current README documents 7 tools (`pack_codebase`, `attach_packed_output`, `pack_remote_repository`, `read_repomix_output`, `grep_repomix_output`, `file_system_read_file`, `file_system_read_directory`). | High | https://github.com/yamadashy/repomix |
| Semgrep | MCP mode | `Client` | Semgrep now documents an MCP Server (beta) plus stdio/SSE/Streamable HTTP transport options with auth setup guidance. | High | https://semgrep.dev/docs/semgrep-mcp |
| SonarQube MCP | Tool count + stars | `34`, `390` | README indicates expanded toolset coverage; treated as `34+` pending exact version-pinned count. Stars refreshed to `391`. | Medium | https://github.com/SonarSource/sonarqube-mcp-server |
| Gemini CLI | Stars + MCP support | `~95k`, `Client` | GitHub now ~95.3k stars; README confirms MCP integration and 1M token context. | High | https://github.com/google-gemini/gemini-cli |
| Codex CLI | Stars + AGENTS/MCP signal | `~61k`, `Client+Server` | GitHub now ~61.4k stars; Codex repo/docs surface AGENTS and MCP usage patterns. | Medium | https://github.com/openai/codex ; https://openai.com/index/introducing-codex/ |
| Aider | Stars + language breadth | `~40k`, `~45` | GitHub now ~40.8k stars; README states support for 100+ languages and codebase mapping behavior. | High | https://github.com/Aider-AI/aider |
| ast-grep | MCP tool count | `4` | MCP docs still list 4 main tools. | High | https://ast-grep.github.io/guide/tools/mcp.html |
| CodeScene MCP | Stars + tools | `20`, `14` | mcp.so listing currently shows 18 stars and 14 tools. | Medium | https://mcp.so/server/codescene-mcp-server/codescene-io |

**Round 6 additions (24 Feb 2026):**

| Competitor | Rechecked Claim | Previous Matrix | Round 6 Finding | Confidence | Source |
|------------|-----------------|-----------------|-----------------|------------|--------|
| CKB/CodeMCP | v8.1.0 features | 76-92 tools, no Tarjan | v8.1.0 (Feb 1) added `findCycles` (Tarjan SCC), `suggestRefactorings`, `planRefactor`, `prepareChange (extract)`, `switchProject`. 10 languages (not 12). | High | https://github.com/SimplyLiz/CodeMCP/wiki |
| SonarQube MCP | v1.10.0 tools | 34+ tools, 391 stars | v1.10.0 (Feb 17) added 7 tools: duplications (2), coverage (2), security hotspots (3). Stars now 393. 3 releases in Feb alone. | High | https://github.com/SonarSource/sonarqube-mcp-server |
| grepai | New competitor | Not tracked | Go-based semantic search + call graphs, 1.3k stars, 5 MCP tools, 9 releases in Feb, RRF hybrid search, Bubble Tea TUI. Very active. | High | https://github.com/yoanbernabeu/grepai |
| Context7 | Star count + scope | 44k stars | 46.7k stars. Remote MCP for library docs (2 tools). NOT code intelligence — complementary. Validates framework pack demand. | High | https://github.com/upstash/context7 |
| CodeGraphMCPServer | Activity | N/A stars, 14 tools | Dormant since Dec 2025. No commits in 2+ months. Threat downgraded to NEGLIGIBLE. | High | https://github.com/nahisaho/CodeGraphMCPServer |
| Augment CE | GA launch | 1 MCP tool | MCP GA launched Feb 6. 1 tool (`codebase-retrieval`). 1000 free calls promo. Still cloud-only. | Medium | https://www.augmentcode.com/product/context-engine-mcp |
| Continue.dev | CLI pivot | IDE extension | Pivoted to CLI (`cn`). MCP consumer, not provider. Async PR agents. | Medium | https://docs.continue.dev/guides/cli |
| Serena | Activity | 40 tools, 20.5k stars | 42 tools (36 loaded, 26 default). JetBrains integration. Memory tools. Daily commits. | High | https://github.com/oraios/serena |
| roam-code | Scoring data | 79/100 | Fixed stale criteria: `structural_pattern_matching` True (+3), `rule_count` now 602 (+1 tier point), `documentation_quality` 2 (+1), plus `dataflow_taint` set to `intra` (+1). Score now 84/100. | High | src/roam/competitor_site_data.py |

**Round 5 matrix policy (still applies):** when competitor docs disagree (example: CKB), track a range and capture the inconsistency explicitly instead of pinning a single number.

---

## Deep-Dive Sections

### SonarQube MCP Server (NEW — Round 2)

**Threat Level: MEDIUM (upgraded from LOW-MEDIUM)**

**What it actually is:** Official first-party Docker-based MCP server by SonarSource. Toolset surface has expanded over time (current docs show 34+ tools and 14+ toolsets). 391 stars, 58 forks, 279 commits. Version 1.10.0.2084.

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

**Key findings:**
- Only `analyze_code_snippet` runs locally (downloads ~200MB analyzers). ALL other tools query running SonarQube server.
- 6,500+ rules across 42+ languages (custom parsers, not tree-sitter)
- SonarQube 2026.1 LTA: Rust support, SCA (SBOM generation), AI CodeFix (cloud LLM)
- AI CodeFix uses Claude Sonnet 4 / GPT-5.1 — NOT local
- Architecture analysis being **deprecated** (Java only, being removed Jan 2026)
- No graph-theoretic analysis, no PageRank, no Louvain, no git forensics

**Where SonarQube MCP wins:** 6,500+ rules, 42+ languages, Quality Gates (flagship), research-backed Cognitive Complexity (they invented it), SCA/SBOM, mature CI/CD ecosystem, 391 stars.

**Where roam wins:** 100% local (zero infrastructure vs Docker+server), graph algorithms, git forensics, architecture analysis (SonarQube deprecated theirs), multi-agent partitioning, zero API keys, zero cost.

---

### Serena MCP (NEW — Round 2)

**Threat Level: LOW-MEDIUM**

**What it actually is:** Coding agent toolkit with a documented list of **40** MCP tools using LSP backends for IDE-grade code navigation. 20,500 stars, 1,400 forks, 117 contributors.

**40 documented tools across 7 categories:**
- Code Navigation (6): `find_symbol`, `find_referencing_symbols`, `get_symbols_overview` + JetBrains variants
- Code Editing (12): `replace_symbol_body`, `insert_after_symbol`, `rename_symbol`, etc.
- Search (1): `search_for_pattern`
- Memory/Session (5): `write_memory`, `read_memory`, `list_memories`, etc.
- System/Workflow (8): `execute_shell_command`, `restart_language_server`, etc.
- Agent Guidance (6): `initial_instructions`, `think_about_task_adherence`, etc.
- Dashboard (1): `open_dashboard`

**Key finding:** Serena is the strongest **interactive coding agent** tool — it helps LLMs navigate and edit code. But it produces ZERO metrics, graphs, security findings, git analytics, or architectural assessments. **Complementary, not competitive.**

---

### CKB/CodeMCP (NEW — Round 2)

**Threat Level: LOW-MEDIUM (closest architectural competitor)**

**What it actually is:** Code intelligence platform with conflicting published counts:
- website: **90+** tools
- MCP tools reference pages: **92** tools
- preset docs references: **76** and **80+** tools (core 14)
Only 59 stars. Go-based, commercial license.

**Preset system (v7.4 docs snapshot):**
| Preset | Tools | Tokens |
|--------|-------|--------|
| core | 14 | ~2,000 |
| review | ~27 | ~3,000 |
| refactor | ~33 | ~4,000 |
| docs | ~27 | ~3,000 |
| ops | ~25 | ~4,000 |
| federation | ~35 | ~4,000 |
| full | 76-80+ | ~10,000 |

**Key tools:** `searchSymbols`, `getSymbol`, `findReferences`, `traceUsage`, `getCallGraph`, `analyzeImpact`, `compareAPI`, `getAffectedTests`, `getArchitecture`, `findDeadCodeCandidates`, `analyzeCoupling`, `getComplexity`, `getHotspots`, `getOwnership`, `getOwnershipDrift`, `scanSecrets`, `explainSymbol`, `expandToolset`

**CKB has that roam doesn't:** multi-repo federation, doc-symbol linking (`docs stale`, `docs coverage`), `suggestRefactorings` proactive recommendations (v8.1.0), `planRefactor` compound refactoring plan (v8.1.0), `prepareChange (extract)` with parameter/return detection (v8.1.0), `justify` explanation tool.

**roam has that CKB doesn't:** PageRank, Louvain clustering, topological layers, topology fingerprinting, spectral bisection, architecture simulation, anomaly detection, vulnerability reachability, SARIF, quality gate presets, 26 languages (vs 10), cognitive complexity, file role classification, cross-language bridges, 169+ YAML rules with AST pattern matching, coverage report ingestion, security hotspot classification, AI debt detection (vibe-check), multi-agent task graph decomposition, developer behavioral profiling, Python 3.9+ support.

**Note (v8.1.0):** CKB added `findCycles` (Tarjan SCC) in v8.1.0, closing one of our previous exclusive advantages. However, they still lack PageRank, Louvain, spectral, layers, fingerprinting, and simulation — the full graph algorithm suite remains a roam exclusive.

---

### Aider Repo-Map (NEW — Round 2)

**Threat Level: LOW (internal only, not exposed)**

**What it actually is:** Aider uses NetworkX PageRank with a **personalization vector** on a file-level identifier-sharing graph. ~40k stars.

**Technical details:**
- Tree-sitter extracts Tags (name, kind=def|ref, file, line) for ~45 languages
- Builds MultiDiGraph: nodes=files, edges=identifier references across files
- Edge weights: chat mentions x10, long identifiers x10, private ids x0.1, chat files x50
- Runs `nx.pagerank()` with personalization boosting files in current conversation
- Token-budget binary search to fit within `max_map_tokens` (default 1024)
- Three-level caching: disk (persistent), map (in-memory), tree (rendered)

**Key distinction:** Aider's graph is file-level (identifier sharing), NOT function-level (call relationships). It is NOT queryable, NOT persistent across sessions as an index, and NOT exposed via MCP. PageRank is used to rank file relevance to current chat, not as an architectural metric.

---

### CodeQL (NEW — Round 2)

**Threat Level: LOW-MEDIUM (different axis)**

**What it actually is:** GitHub's SAST tool with the deepest graph analysis in security (Code Property Graph with CFG+DFG). No official MCP server. 78 CLI subcommands. 12 languages.

**CodeQL's graphs vs roam's:**
| Aspect | CodeQL | roam-code |
|--------|--------|-----------|
| Graph type | Code Property Graph (AST+CFG+DFG) | Dependency/call graph (NetworkX) |
| Purpose | Security vulnerability detection | Architecture comprehension |
| Call graph | Context-sensitive, cross-file | Tree-sitter extracted |
| Data flow | Local + global, source-to-sink | No |
| Taint tracking | Yes, with flow-state labels | No |
| PageRank/centrality | No | Yes |
| Community detection | No | Yes (Louvain) |
| Topological layers | No | Yes |
| Architecture simulation | No | Yes |
| Git integration | None (point-in-time) | Full (churn, blame, entropy) |

**Key insight:** CodeQL answers "is this code exploitable?" roam answers "how is this codebase structured?" Fundamentally different problems. No overlap.

**Pricing:** Free for OSS. Private repos: $30/committer/month (Code Security) + $19/committer/month (Secret Protection). Requires GitHub Advanced Security.

---

### ast-grep (NEW — Round 2)

**Threat Level: LOW (orthogonal strength)**

**What it actually is:** Rust-based structural code search/rewrite engine. 12,600 stars. 26 built-in languages via tree-sitter. Highly expressive YAML rule system.

**4 MCP tools (experimental):** `dump_syntax_tree`, `test_match_code_rule`, `find_code`, `find_code_by_rule`

**Key strengths roam lacks:** Deep structural pattern matching with relational operators (`inside`, `has`, `follows`, `precedes`), template-based code rewriting, YAML rule system, Rust performance (sub-second scans).

**What ast-grep lacks:** Everything analytical — no index, no graphs, no metrics, no git history, no architecture analysis. Purely a per-invocation scan tool.

**Now has SARIF output** (v0.40.0, Nov 2025).

---

### Repomix (NEW — Round 2)

**Threat Level: NONE (different category)**

**What it actually is:** Context packing tool that concatenates files into LLM-friendly format. 22,000 stars. 7 MCP tools documented in current README. Zero analysis capabilities.

**Current MCP tools (README):**
- `pack_codebase`
- `attach_packed_output`
- `pack_remote_repository`
- `read_repomix_output`
- `grep_repomix_output`
- `file_system_read_file`
- `file_system_read_directory`

**Why 22k stars:** Zero-friction ("pack repo for AI"), web interface at repomix.com, browser extension, remote repo support, immediate value proposition. Proves that simple + focused + zero-friction = massive adoption.

---

## Agent Platform Gap Analysis

Every major AI coding platform lacks structural code intelligence that roam provides:

| Platform | Stars | #1 Gap roam Fills | Their Best Built-in |
|----------|-------|-------------------|-------------------|
| **Claude Code** | 68.3k | No persistent index, no call graph, no blast radius | Glob, Grep, Read, Agent Teams |
| **Codex CLI** | 61.4k | No codebase indexing (#1 recognized limitation per GitHub issues) | Shell commands (rg, find) |
| **Gemini CLI** | 95.3k | No persistent index (most-requested feature), no typed property graph | Codebase Investigator (LLM-driven) |
| **Amp** | N/A | No local graph algorithms, credit-based pricing | Sourcegraph code graph (cloud) |
| **Cursor** | N/A | No call/dependency graph (biggest multi-file refactor limitation) | Cloud embeddings, semantic search |
| **Windsurf** | N/A | No deterministic analysis (Codemaps = AI-generated, non-reproducible) | Cascade, Codemaps (Mermaid) |

**Common gap across ALL platforms:** None has PageRank, Tarjan SCC, Louvain clustering, cognitive complexity, git churn/co-change, architecture simulation, or vulnerability reachability. **Every platform has text search; none has structural intelligence.**

---

## New MCP Competitors Discovered (Round 2)

| Tool | Stars | MCP Tools | Key Feature | Threat |
|------|-------|-----------|-------------|--------|
| **CodePrism** (Rust, MIT) | New | 18 | Graph-based, 1000+ files/sec, AI-generated | LOW |
| **code-graph-mcp** | 80 | 9 | Claims PageRank at 4.9M nodes/sec, ast-grep backend | LOW |
| **CodeGraphMCPServer** | N/A | 14 | **Louvain** + GraphRAG, OpenAI/Anthropic integration | LOW-MED |
| **Code Pathfinder** | N/A | 6 | Call graphs, dependency tracing, dataflow analysis | LOW |
| **FalkorDB Code Graph** | N/A | N/A | GraphRAG, Java+Python only | VERY LOW |
| **CodeRLM** | 162 | 0 (REST) | Rust server, 8 languages, 14 commits, prototype | VERY LOW |

**Note:** CodeGraphMCPServer has Louvain community detection — making it and roam-code the only two MCP servers with graph algorithm support. However, it requires LLM API keys and has no git forensics, SARIF, or architecture simulation.

---

## AI Code Quality Competitors

| Tool | What It Does | Threat | MCP? |
|------|-------------|--------|------|
| **OX VibeSec** ($60M B) | Prevents vulns at AI code generation time (10 anti-patterns) | LOW (different layer) | Yes |
| **Codacy AI Risk Hub** | AI governance dashboard, risk scoring, policy enforcement | LOW-MED | Yes |
| **TurinTech Artemis** | Evolutionary AI code optimization, Intel partnership | LOW | No |
| **GitClear** | Dev analytics dashboard (211M lines study), not a tool | VERY LOW | No |
| **DeepWiki** (Cognition) | AI-generated wiki/docs/Mermaid for repos | LOW-MED (indirect) | Yes |
| **Qodo 2.1** | Continuous learning rules for AI code review | LOW-MED | Unknown |
| **CodeRabbit** | AI PR reviewer, 2M+ repos, pre-configured MCP servers | NONE | Client |

---

## Recent Moves (Feb 2026 — Updated)

| Event | Impact on roam-code |
|-------|---------------------|
| **MCP donated to Linux Foundation (AAIF)** — Anthropic, Block, OpenAI co-founding. AWS, Google, Microsoft platinum members. | Legitimizes MCP investment. Get listed NOW. |
| **Official MCP Registry: 518 servers** (grew from 90 in 1 month) | Window closing fast. #31 is critical. |
| **PulseMCP: 8,610+ servers** | Largest directory. Must be listed. |
| **41% of MCP servers lack authentication** (security audit Feb 21) | Our "100% local, zero API keys" is a MASSIVE security differentiator. |
| **SonarQube MCP: 34+ tools, 391 stars** | Bigger than expected but requires Docker+server. Position as "zero-infra alternative." |
| **Claude Code: 68.3k stars, 4% of GitHub commits** | #1 agent platform. Our MCP tools are perfectly positioned for it. |
| **Gemini CLI: 95.3k stars, 1M token context** | Huge adoption but #1 feature request is persistent codebase indexing (which roam has). |
| **Semgrep MCP Server (beta) now documented with transport/auth guidance** | Matrix should treat Semgrep as MCP server-capable, not client-only, and keep remote transport compatibility in scope. |
| **Repomix MCP server docs list 7 tools** | Context packing category is evolving faster than older matrix snapshots. |
| **CKB published tool counts conflict (76/80+ vs 90+/92)** | Track as a range and avoid single-number claims without version pinning. |
| **VS Code multi-agent** (Feb 2026, v1.110) | Multiple agents in parallel = need for roam's `orchestrate` partitioning. |
| **Cognition acquired Windsurf** for $250M | Devin + Windsurf integration. Codemaps validates Mermaid diagram demand. |
| **Sourcegraph → Amp split**, Cody Free killed | "Lost Cody Free? roam-code is free forever." |
| **JetBrains MCP support** (v2025.2) | New distribution channel for roam MCP. |
| **GitHub Copilot MCP integration** | Agent mode connects to MCP servers. Ensure roam works seamlessly. |
| **Kiro** (AWS) — spec-driven IDE, GovCloud | New agent IDE in the market. |
| **CodeRabbit: AI code 1.7x more issues, 3x readability problems** | Validates demand for code quality tools. |
| **MCP-Atlas benchmark: best model 62.3% on multi-tool tasks** | Validates our compound operations approach (fewer tool calls = higher success). |
| **MCP Tool Smells paper: 97.1% of tools have smells** | Our <60 token descriptions are ahead of field. |
| **MCP current revision moved to 2025-11-25** | Update transport/auth assumptions; align roadmap around current spec language. |
| **Copilot coding agent MCP is tools-only (no prompts/resources)** | Prioritize tools-only compatibility profile and fallback behavior in conformance tests. |
| **VS Code supports `AGENTS.md` as the canonical custom-instruction file** | Keep `AGENTS.md` as canonical export target; use provider overlays where client docs explicitly support them. |
| **CKB v8.1.0 (Feb 1): `findCycles`, `suggestRefactorings`, `planRefactor`, `prepareChange (extract)`** | CKB directly tracking our feature categories. They added Tarjan SCC, proactive refactoring recommendations, and compound refactoring planning. Validates our feature set; we must add composition commands (#140, #141). |
| **SonarQube MCP v1.10.0 (Feb 17): 7 new tools** | Added duplications, coverage details, security hotspots, PR list. 3 releases in Feb alone. Token optimization work shows agent UX focus. Still requires Docker+server. |
| **grepai: 9 releases in Feb, 1.3k stars, growing fast** | Go-based semantic search + call graph tracing. RRF hybrid search, Bubble Tea TUI, workspace mode. Only 5 MCP tools but shipping velocity is highest in the space. Strictly search-focused — no architecture analysis. |
| **Context7 (Upstash): 46.7k stars** | Remote MCP server for library/framework docs. NOT code intelligence — complementary. Validates demand for framework knowledge packs (#96). |
| **CodeGraphMCPServer: dormant since Dec 2025** | No activity in 2+ months. Threat level downgraded to NEGLIGIBLE. |
| **Augment CE MCP: GA launch Feb 6** | Context Engine MCP now generally available. 1 tool (`codebase-retrieval`). 1000 free calls promo in Feb. Cloud-only. |
| **Continue.dev pivot to CLI (`cn`)** | Open-source CLI running async agents on every PR. MCP consumer, not provider. Validates CLI-based code intelligence market. |
| **Serena: JetBrains integration, memory tools** | Daily commits, 20.5k stars. LSP-first editing toolkit. Complementary, not competitive. |
| **Scoring data correction: roam 79→84/100** | Fixed stale `structural_pattern_matching` (False→True, #121 shipped), `rule_count` (20→602 after #145 pack expansion), `documentation_quality` (1→2, #132 shipped), and `dataflow_taint` (`none`→`intra`, #142 shipped). |

---

## Positioning Map

```
                        HIGH Architecture Analysis Depth
                                    |
                                    |
                       CodeScene    |   roam-code  <-- UNIQUE POSITION
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
         Sourcegraph/Amp            |   (graph+LLM, shallow algos)
         (search)                   |   Cursor, Windsurf
                                    |   Augment, Continue
              Repomix               |   Claude Code, Codex CLI
              (packing)             |   Gemini CLI
                                    |
                        LOW Architecture Analysis Depth
```

---

## What NOBODY Else Does (roam-code Exclusives)

1. **134 canonical CLI commands (+1 legacy alias)** from a single local SQLite index
2. **Architecture simulation** — "what if I move this symbol?" with predicted metric changes
3. **Multi-agent work partitioning** — Louvain-based graph decomposition for parallel agent work
4. **Algorithm anti-pattern detection** — 23 task catalog with ranked solution approaches
5. **Topology fingerprinting** — compare architectural snapshots over time
6. **PR attestation** — proof-carrying quality assertions
7. **Combined PageRank + Louvain + spectral bisection + topological layers** — no other tool has all four (CKB added Tarjan SCC in v8.1.0 but lacks the rest)
8. **Git archaeology + graph algorithms + architecture analysis** in one local tool with zero API keys
9. **600+ structural rules** with AST pattern matching (`$WILDCARD` metavariables) and language-scoped style governance — ast-grep-style power in a full analysis platform
10. **AI debt detection suite** — vibe-check (8-pattern AI rot), ai-ratio estimation, trend cohort analysis (AI vs human quality trajectories)

---

## Where roam-code Trails

1. **CI/CD breadth** — GitHub Action shipped; still behind on GitLab/Azure/Bitbucket/Jenkins coverage
2. **GitHub stars/visibility** — 286 vs 95.3k (Gemini), 68k (Claude Code), 46.7k (Context7), 40.8k (Aider), 22k (Repomix), 20.5k (Serena)
3. **Language breadth** — 26 vs 42+ (SonarQube), 30+ (Serena LSP), 100+ (Aider linting)
4. **IDE integration** — pure CLI, no VS Code/JetBrains plugin
5. **Dataflow analysis** — no intra/inter-procedural dataflow; biggest single scoring gap (4pts). SonarQube and CodeQL have full taint tracking. Planned #142 (basic intra-procedural)
6. **Rule pack depth** — 602 rules vs 3,500+ (Semgrep), 6,500+ (SonarQube). #145 reached 500+; next depth milestone remains 1000+
7. **Proactive refactoring intelligence** — CKB v8.1.0 shipped `suggestRefactorings` and `planRefactor`. Planned #140, #141
8. **Documentation intelligence** — CKB has `docs coverage`/`docs stale`. Planned #143

---

## The Competitive Moat

No competitor can easily replicate roam-code's position because it requires:
- Tree-sitter AST parsing (shared with Aider, ast-grep, CodeGraphContext)
- PLUS persistent SQLite indexing (unique combination — no external DB)
- PLUS NetworkX graph algorithms (PageRank, Tarjan, Louvain, topological layers)
- PLUS git history analysis (shared with CodeScene)
- PLUS MCP server for agent consumption (many have this)
- PLUS zero cloud dependency (shared with ast-grep, Semgrep)
- PLUS architecture simulation & multi-agent partitioning (unique)

**All seven together? Only roam-code.**

---

## Threat Assessment (Updated)

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| CKB grows community + adds graph algos | Low-Medium | High | Ship faster; CKB has 59 stars, Go-only incremental, commercial license |
| SonarQube MCP gains agent market share | Medium | Medium | Different positioning (infra-heavy quality gates vs zero-infra comprehension) |
| Claude Code adds persistent indexing | Medium | High | Become THE MCP provider for Claude Code; they want MCP tools, not built-in |
| Aider deepens repo-map to function-level | Low | Medium | 115-command lead + architecture analysis is unreplicable |
| Cursor/Windsurf add structural graph | Low | Medium | IDE-first tools rarely go deep on CLI-native analysis |
| Serena adds metrics/architecture | Low | Medium | LSP-first architecture doesn't lend itself to graph analysis |
| CodeGraphMCPServer adds full algorithm suite | Low-Medium | Medium | We have Tarjan SCC, layers, simulation, git forensics they don't |
| New entrant with $25M+ funding | Medium | High | Ship GitHub Action + grow community before funded competitors emerge |

---

## Positioning One-Liners (Updated)

| vs Competitor | One-liner |
|--------------|-----------|
| vs SonarQube MCP | "90 vs 34+ tools, zero infrastructure vs Docker+server, graph algorithms they deprecated" |
| vs CKB/CodeMCP | "PageRank+Louvain+spectral+layers they don't have, 26 vs 10 languages, 602 rules vs 0, free vs commercial" |
| vs Serena MCP | "Structural analysis vs LSP navigation — roam understands architecture, Serena helps you edit" |
| vs Augment CE | "90x more tools, 100% local, zero API keys, open source" |
| vs CodeScene MCP | "Free vs EUR 18-27/dev/mo, 90 vs 14 tools, graph algorithms they don't have" |
| vs CodeGraphContext | "26 vs 16 languages, SQLite vs external graph DB, PageRank+Tarjan+Louvain" |
| vs Greptile | "Deterministic vs LLM prose, 100% local vs cloud-dependent, free vs $30/dev/mo" |
| vs Claude Context | "90 vs 4 tools, graph algorithms vs vector search, zero cloud dependency" |
| vs CodeQL | "Architecture comprehension vs security scanning — complementary, not competitive" |
| vs Repomix | "Analysis vs packing — roam understands code, Repomix concatenates it" |

---

## Features Worth Deepening/Adopting (Updated)

| Priority | Feature | Source | Backlog Item | Status |
|----------|---------|--------|-------------|--------|
| **HIGH** | Proactive refactoring recommendations | CKB v8.1.0 `suggestRefactorings` | `#140` | Planned (NEW) |
| **HIGH** | Compound refactoring plan | CKB v8.1.0 `planRefactor` | `#141` | Planned (NEW) |
| **HIGH** | Basic intra-procedural dataflow | SonarQube/CodeQL scoring gap | `#142` | Planned (NEW) |
| **MEDIUM** | Documentation coverage/staleness tools | CKB `docs stale/coverage` | `#143` | Planned (NEW) |
| **MEDIUM** | Refactoring ROI estimation | CodeScene business case | `#144` | Planned (NEW) |
| **HIGH** | Rule pack depth expansion to 1000+ | Semgrep/SonarQube rule depth | `#145` | 500+ shipped; 1000+ follow-up |
| **MEDIUM** | Daemon/webhook warm-index flow | CKB | `#95` | Shipped |
| **DONE** | Streamable HTTP MCP transport | MCP specification updates | `#98` | Shipped (baseline) |
| **DONE** | Full MCP tool annotations + taskSupport metadata | MCP schema + tools semantics | `#99` | Shipped |
| **DONE** | MCP client conformance profile suite (Copilot/Gemini/Claude/Codex) | GitHub Copilot + Gemini CLI docs | `#100` | Shipped |
| **DONE** | Mermaid architecture diagrams | DeepWiki, Windsurf, Greptile | `#82` | Shipped |
| **DONE** | AI code anti-pattern detection (10 patterns with prevalence) | OX VibeSec | `#57` | Shipped |
| **DONE** | `defer_loading: true` for Claude Code Tool Search | Claude Code | `#66` | Shipped |
| **DONE** | Multi-agent context export files (`AGENTS.md` baseline + overlays) | GitHub/Codex/VS Code/Claude/Gemini docs | `#68`, `#97` | Shipped |
| **MEDIUM** | Agent performance benchmark (MCP-Atlas methodology) | MCP-Atlas paper | `#35` | Planned |
| **DONE** | Secret scanner pattern-depth expansion | CKB | `#133` | Shipped (entropy + env-var + remediation) |
| **DONE** | Hybrid BM25 + neural embedding search | SquirrelSoft, Claude Context | `#54` | Shipped (RRF fusion) |
| **DONE** | Conversation-aware PageRank (personalization vector) | Aider repo-map | `#94` | Shipped |
| **MEDIUM** | Pre-indexed bundles for popular libraries | CodeGraphContext, Context7 (46.7k stars) | `#96` | Planned (elevated — Context7 growth validates demand) |
| **DONE** | Live file watching with auto-reindex | CodeGraphContext, SquirrelSoft | `#60` | Shipped |
| **DONE** | Structural pattern matching rules (YAML) | ast-grep | `#93`, `#121`, `#136` | Shipped (169 rules + AST metavariables) |
| **LOW** | Multi-repo federation | CKB | Someday/Maybe | Deferred |
| **LOW** | Zero-friction web demo (URL replacement) | DeepWiki | Someday/Maybe | Deferred |

---

## MCP Ecosystem Context

- **Current protocol revision:** 2025-11-25
- **Official MCP Registry:** 518 servers (90 → 518 in 1 month)
- **PulseMCP:** 8,610+ servers (largest directory)
- **Total ecosystem:** 17,000+ servers
- **SDK downloads:** 97M+ monthly
- **Governance:** Linux Foundation (AAIF), co-founded by Anthropic + Block + OpenAI
- **Security:** 41% of registry servers lack authentication (Feb 2026 audit)
- **Tool quality:** 97.1% of MCP tool descriptions have smells (arXiv:2602.14878)
- **Agent success:** Best model achieves 62.3% on multi-tool tasks (MCP-Atlas)
- **Key paper findings:** Shorter targeted descriptions beat verbose ones; compound operations reduce failure rates

---

## Round 5 Primary Sources (Matrix Recheck)

- https://docs.codeknowledge.dev/mcp/getting-started/mcp-integration
- https://docs.codeknowledge.dev/mcp/getting-started/mcp-tools-reference
- https://www.codeknowledge.dev/pricing
- https://oraios.github.io/serena/docs/mcp_guide/available_tools/
- https://github.com/yamadashy/repomix
- https://semgrep.dev/docs/semgrep-mcp
- https://github.com/google-gemini/gemini-cli
- https://github.com/openai/codex
- https://openai.com/index/introducing-codex/
- https://github.com/Aider-AI/aider
- https://github.com/SonarSource/sonarqube-mcp-server
- https://ast-grep.github.io/guide/tools/mcp.html
- https://mcp.so/server/codescene-mcp-server/codescene-io
- src/roam/surface_counts.py
- tests/test_surface_counts.py

---

## Detailed Reports Index

| File | Scope |
|------|-------|
| `reports/competitors/01_cursor.md` | Cursor |
| `reports/competitors/02_windsurf.md` | Windsurf |
| `reports/competitors/03_claude_code.md` | Claude Code |
| `reports/competitors/04_codex_cli.md` | Codex CLI |
| `reports/competitors/05_gemini_cli.md` | Gemini CLI |
| `reports/competitors/06_aider.md` | Aider |
| `reports/competitors/07_sonarqube.md` | SonarQube |
| `reports/competitors/08_codeql.md` | CodeQL |
| `reports/competitors/09_semgrep_astgrep.md` | Semgrep + ast-grep |
| `reports/competitors/10_greptile_codescene.md` | Greptile + CodeScene |
| `reports/competitors/11_sourcegraph.md` | Sourcegraph |
| `reports/competitors/12_serena.md` | Serena MCP |
| `reports/competitors/13_repomix.md` | Repomix |
| `reports/competitors/14_continue_dev.md` | Continue.dev |
| `reports/competitors/15_ckb_codemcp.md` | CKB + CodeMCP |
| `reports/competitors/16_squirrelsoft_code_index.md` | SquirrelSoft Code Index |
| `reports/competitors/15b-f_*_verification.md` | 5 verification deep-dives |
| `reports/competitors/augment_context_engine_mcp.md` | Augment CE deep-dive |
| `reports/competitors/greptile_v3_deep_dive.md` | Greptile v3 deep-dive |

---

*Source: 16 Opus research agents + Round 6 deep-dive, 200+ web sources, Feb 24 2026*
*Competitive score: 84/100 (nearest: SonarQube 63, CodeQL 49, Semgrep 45)*
*Target: 85/100 after crossing 1000+ rules (`#145` next tier milestone)*
