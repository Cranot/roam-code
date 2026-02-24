"""Generate site data for the competitive landscape page.

Source of truth:
- reports/competitor_tracker.md (matrix + decision tables)
- SCORING_RUBRIC (7 categories, 45 binary/tiered criteria, 100 pts total)
- CRITERIA_DATA (per-competitor criterion values)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


# Tools included in the landscape page (code intelligence tools only).
# Agents, IDEs, context packagers, and structural-grep utilities are excluded.
LANDSCAPE_INCLUDE = {
    "roam-code",
    "CKB/CodeMCP",
    "CodeGraphMCPServer",
    "CodePrism",
    "Sourcegraph/Amp",
    "CodeScene",
    "SonarQube",
    "CodeQL",
    "Semgrep",
}


# ---------------------------------------------------------------------------
# Scoring rubric: 7 categories, 45 criteria, 100 points total.
# Each criterion is binary (True/False → max or 0), tiered (raw value mapped
# through ranges), or subjective (direct 0..max).
# ---------------------------------------------------------------------------

SCORING_RUBRIC = [
    {
        "id": "static_analysis",
        "label": "Static Analysis Depth",
        "max_points": 20,
        "default_weight": 20,
        "criteria": [
            {"id": "ast_parsing", "label": "AST parsing", "type": "binary", "max": 2},
            {"id": "persistent_index", "label": "Persistent index", "type": "binary", "max": 3},
            {"id": "call_graph", "label": "Call graph extraction", "type": "tiered", "max": 3,
             "tiers": {"none": 0, "basic": 1, "cross_file": 2, "dataflow": 3}},
            {"id": "cognitive_complexity", "label": "Cognitive complexity metrics", "type": "binary", "max": 2},
            {"id": "vuln_reachability", "label": "Vulnerability reachability", "type": "tiered", "max": 2,
             "tiers": {"none": 0, "graph": 1, "taint": 2}},
            {"id": "dead_code", "label": "Dead code detection", "type": "tiered", "max": 2,
             "tiers": {"none": 0, "reference": 1, "dataflow": 2}},
            {"id": "dataflow_taint", "label": "Dataflow/taint analysis", "type": "tiered", "max": 4,
             "tiers": {"none": 0, "intra": 1, "inter": 3, "full": 4}},
            {"id": "languages_supported", "label": "Languages supported", "type": "tiered", "max": 2,
             "tiers": [[0, 4, 0], [5, 15, 1], [16, None, 2]]},
        ],
    },
    {
        "id": "graph_intelligence",
        "label": "Graph Intelligence",
        "max_points": 10,
        "default_weight": 10,
        "criteria": [
            {"id": "pagerank_centrality", "label": "PageRank / centrality", "type": "binary", "max": 2},
            {"id": "cycle_detection_scc", "label": "Cycle detection (SCC)", "type": "binary", "max": 2},
            {"id": "community_detection", "label": "Community detection (Louvain)", "type": "binary", "max": 2},
            {"id": "topological_layers", "label": "Topological layers", "type": "binary", "max": 1},
            {"id": "architecture_simulation", "label": "Architecture simulation", "type": "binary", "max": 2},
            {"id": "topology_fingerprint", "label": "Topology fingerprinting", "type": "binary", "max": 1},
        ],
    },
    {
        "id": "git_temporal",
        "label": "Git & Temporal Analysis",
        "max_points": 10,
        "default_weight": 10,
        "criteria": [
            {"id": "git_churn_hotspots", "label": "Git churn / hotspots", "type": "binary", "max": 3},
            {"id": "co_change_coupling", "label": "Co-change coupling", "type": "binary", "max": 2},
            {"id": "blame_ownership", "label": "Blame / ownership", "type": "binary", "max": 2},
            {"id": "entropy_analysis", "label": "Entropy analysis", "type": "binary", "max": 2},
            {"id": "pr_diff_risk", "label": "PR/diff risk scoring", "type": "binary", "max": 1},
        ],
    },
    {
        "id": "agent_integration",
        "label": "Agent Integration",
        "max_points": 16,
        "default_weight": 16,
        "criteria": [
            {"id": "mcp_tools_count", "label": "MCP tools", "type": "tiered", "max": 6,
             "tiers": [[0, 0, 0], [1, 10, 2], [11, 30, 4], [31, None, 6]]},
            {"id": "json_structured_output", "label": "JSON structured output", "type": "binary", "max": 2},
            {"id": "token_budget", "label": "Token budget awareness", "type": "binary", "max": 2},
            {"id": "tool_presets", "label": "Tool presets / progressive disclosure", "type": "binary", "max": 2},
            {"id": "cli_commands_count", "label": "CLI commands", "type": "tiered", "max": 3,
             "tiers": [[0, 4, 0], [5, 20, 1], [21, 50, 2], [51, None, 3]]},
            {"id": "compound_batch", "label": "Compound/batch operations", "type": "binary", "max": 1},
        ],
    },
    {
        "id": "security_governance",
        "label": "Security & Governance",
        "max_points": 15,
        "default_weight": 15,
        "criteria": [
            {"id": "vuln_scanning", "label": "Vulnerability scanning", "type": "binary", "max": 3},
            {"id": "reachability_from_vulns", "label": "Reachability from vulns", "type": "binary", "max": 3},
            {"id": "secret_detection", "label": "Secret detection", "type": "tiered", "max": 3,
             "tiers": {"none": 0, "regex": 1, "semantic": 2, "remediation": 3}},
            {"id": "quality_gates", "label": "Quality gates / policy rules", "type": "binary", "max": 3},
            {"id": "rule_count", "label": "Rule count", "type": "tiered", "max": 3,
             "tiers": [[0, 99, 0], [100, 999, 1], [1000, 4999, 2], [5000, None, 3]]},
        ],
    },
    {
        "id": "ecosystem",
        "label": "Ecosystem & Accessibility",
        "max_points": 19,
        "default_weight": 19,
        "criteria": [
            {"id": "open_source", "label": "Open source", "type": "tiered", "max": 2,
             "tiers": {"full": 2, "partial": 1, "none": 0}},
            {"id": "free_tier", "label": "Free tier", "type": "binary", "max": 2},
            {"id": "github_stars", "label": "GitHub stars", "type": "tiered", "max": 3,
             "tiers": [[0, 999, 0], [1000, 9999, 1], [10000, 49999, 2], [50000, None, 3]]},
            {"id": "ci_cd_integration", "label": "CI/CD integration", "type": "binary", "max": 2},
            {"id": "ide_integration", "label": "IDE integration", "type": "binary", "max": 2},
            {"id": "multi_platform", "label": "Multi-platform", "type": "binary", "max": 1},
            {"id": "documentation_quality", "label": "Documentation quality", "type": "subjective", "max": 2},
            {"id": "active_maintenance", "label": "Active maintenance (90 days)", "type": "binary", "max": 1},
            {"id": "sarif_output", "label": "SARIF output", "type": "binary", "max": 2},
            {"id": "local_zero_api", "label": "100% local / zero API keys", "type": "binary", "max": 2},
        ],
    },
    {
        "id": "unique_capabilities",
        "label": "Unique Capabilities",
        "max_points": 10,
        "default_weight": 10,
        "criteria": [
            {"id": "multi_agent_partitioning", "label": "Multi-agent partitioning", "type": "binary", "max": 1},
            {"id": "structural_pattern_matching", "label": "Structural pattern matching", "type": "binary", "max": 3},
            {"id": "semantic_search", "label": "Embedding / semantic search", "type": "binary", "max": 2},
            {"id": "multi_repo_federation", "label": "Multi-repo federation", "type": "binary", "max": 3},
            {"id": "daemon_live_watch", "label": "Daemon / live-watch mode", "type": "binary", "max": 1},
        ],
    },
]


# ---------------------------------------------------------------------------
# Per-competitor criterion values.  Binary → True/False.  Tiered → raw value
# (int or str).  Subjective → int 0..max.  Missing keys default to False/0.
# ---------------------------------------------------------------------------

CRITERIA_DATA: dict[str, dict[str, object]] = {
    "roam-code": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "cross_file",
        "cognitive_complexity": True, "vuln_reachability": "graph", "dead_code": "reference",
        "dataflow_taint": "intra", "languages_supported": 26,
        "pagerank_centrality": True, "cycle_detection_scc": True,
        "community_detection": True, "topological_layers": True,
        "architecture_simulation": True, "topology_fingerprint": True,
        "git_churn_hotspots": True, "co_change_coupling": True,
        "blame_ownership": True, "entropy_analysis": True, "pr_diff_risk": True,
        "mcp_tools_count": 101, "json_structured_output": True, "token_budget": True,
        "tool_presets": True, "sarif_output": True, "cli_commands_count": 136,
        "compound_batch": True, "local_zero_api": True,
        "vuln_scanning": True, "reachability_from_vulns": True,
        "secret_detection": "remediation", "quality_gates": True, "rule_count": 602,
        "open_source": "full", "free_tier": True, "github_stars": 200,
        "ci_cd_integration": True, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": True, "structural_pattern_matching": True,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": True,
    },
    "CKB/CodeMCP": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "cross_file",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "reference",
        "dataflow_taint": "none", "languages_supported": 12,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 76, "json_structured_output": True, "token_budget": True,
        "tool_presets": True, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": True, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "regex", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 400,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": True,
    },
    "Serena MCP": {
        "ast_parsing": True, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 30,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 40, "json_structured_output": True, "token_budget": False,
        "tool_presets": True, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 20500,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "SonarQube": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "dataflow",
        "cognitive_complexity": True, "vuln_reachability": "taint", "dead_code": "dataflow",
        "dataflow_taint": "full", "languages_supported": 30,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 34, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": True, "cli_commands_count": 5,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": True, "reachability_from_vulns": True,
        "secret_detection": "remediation", "quality_gates": True, "rule_count": 6500,
        "open_source": "partial", "free_tier": True, "github_stars": 10200,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": True,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": True,
    },
    "CodeQL": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "dataflow",
        "cognitive_complexity": False, "vuln_reachability": "taint", "dead_code": "dataflow",
        "dataflow_taint": "full", "languages_supported": 12,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": True, "cli_commands_count": 10,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": True, "reachability_from_vulns": True,
        "secret_detection": "semantic", "quality_gates": True, "rule_count": 3000,
        "open_source": "partial", "free_tier": True, "github_stars": 8000,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Semgrep": {
        "ast_parsing": True, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "inter", "languages_supported": 30,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 3, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": True, "cli_commands_count": 10,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": True, "reachability_from_vulns": True,
        "secret_detection": "semantic", "quality_gates": True, "rule_count": 3500,
        "open_source": "partial", "free_tier": True, "github_stars": 11000,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": True,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "ast-grep": {
        "ast_parsing": True, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 15,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 4, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 8,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 5000,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": True,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Augment Code": {
        "ast_parsing": False, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": True,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "none", "free_tier": False, "github_stars": 0,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": False,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Cursor": {
        "ast_parsing": False, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 5, "json_structured_output": True, "token_budget": True,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "none", "free_tier": False, "github_stars": 0,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": False,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Windsurf": {
        "ast_parsing": False, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": True,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "none", "free_tier": False, "github_stars": 0,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": False,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Claude Code": {
        "ast_parsing": False, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": True,
        "tool_presets": True, "sarif_output": False, "cli_commands_count": 10,
        "compound_batch": True, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 25000,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Codex CLI": {
        "ast_parsing": False, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 5,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": False, "github_stars": 15000,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Gemini CLI": {
        "ast_parsing": False, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 5, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 10,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 50000,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Aider": {
        "ast_parsing": True, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": True, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": False, "token_budget": True,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 10,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 25000,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Continue.dev": {
        "ast_parsing": False, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 5, "json_structured_output": True, "token_budget": True,
        "tool_presets": True, "sarif_output": False, "cli_commands_count": 5,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 20000,
        "ci_cd_integration": False, "ide_integration": True, "multi_platform": False,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Sourcegraph/Amp": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "basic",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 20,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 10,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "partial", "free_tier": False, "github_stars": 10000,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 2, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": True, "daemon_live_watch": False,
    },
    "CodePrism": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "basic",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 20, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 200,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Greptile": {
        "ast_parsing": False, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 10,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "none", "free_tier": False, "github_stars": 3000,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": False,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": True, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "CodeScene": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": True, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 15,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": True, "co_change_coupling": True,
        "blame_ownership": True, "entropy_analysis": False, "pr_diff_risk": True,
        "mcp_tools_count": 11, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 5,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": True, "rule_count": 50,
        "open_source": "none", "free_tier": False, "github_stars": 500,
        "ci_cd_integration": True, "ide_integration": True, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "Repomix": {
        "ast_parsing": False, "persistent_index": False, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 0, "json_structured_output": True, "token_budget": True,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 5,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 20000,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
    "CodeGraphMCPServer": {
        "ast_parsing": True, "persistent_index": True, "call_graph": "basic",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 12,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": True, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 14, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": True,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 200,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": True,
    },
    "Context7": {
        "ast_parsing": False, "persistent_index": True, "call_graph": "none",
        "cognitive_complexity": False, "vuln_reachability": "none", "dead_code": "none",
        "dataflow_taint": "none", "languages_supported": 0,
        "pagerank_centrality": False, "cycle_detection_scc": False,
        "community_detection": False, "topological_layers": False,
        "architecture_simulation": False, "topology_fingerprint": False,
        "git_churn_hotspots": False, "co_change_coupling": False,
        "blame_ownership": False, "entropy_analysis": False, "pr_diff_risk": False,
        "mcp_tools_count": 2, "json_structured_output": True, "token_budget": False,
        "tool_presets": False, "sarif_output": False, "cli_commands_count": 0,
        "compound_batch": False, "local_zero_api": False,
        "vuln_scanning": False, "reachability_from_vulns": False,
        "secret_detection": "none", "quality_gates": False, "rule_count": 0,
        "open_source": "full", "free_tier": True, "github_stars": 44000,
        "ci_cd_integration": False, "ide_integration": False, "multi_platform": True,
        "documentation_quality": 1, "active_maintenance": True,
        "multi_agent_partitioning": False, "structural_pattern_matching": False,
        "semantic_search": False, "multi_repo_federation": False, "daemon_live_watch": False,
    },
}


# Visual placement + positioning copy are not present in markdown tables.
# Keep these explicit and fail generation if a competitor in the matrix is missing here.
# arch/agent are computed from CRITERIA_DATA via compute_scores().
MAP_METADATA: dict[str, dict[str, object]] = {
    "roam-code": {
        "category": "mcp_server",
        "status": "Graph-first code intelligence CLI + MCP server",
        "relationship": "self",
        "peer": True,
        "graph": "PageRank + Tarjan + Louvain + layers",
        "note": "Graph algorithms (PageRank, SCC, Louvain, Fiedler) on tree-sitter ASTs fused with git history in SQLite. 99 MCP tools, 134 CLI commands.",
        "version_evaluated": "11.0.0",
        "repo_url": "https://github.com/Cranot/roam-code",
    },
    "CKB/CodeMCP": {
        "category": "mcp_server",
        "status": "SCIP-based semantic code intelligence",
        "relationship": "direct_competitor",
        "peer": True,
        "graph": "SCIP symbol graph",
        "note": "SCIP-based semantic indexing with compound and batch MCP operations. Precise symbol resolution across 12+ languages.",
        "mcp_note": "MCP count varies across sources (76-92 reported); core unique tools ~14.",
        "version_evaluated": "v8.1.0",
        "repo_url": "https://github.com/AbanteAI/codeMCP",
    },
    "Serena MCP": {
        "category": "mcp_server",
        "status": "High agent-channel relevance",
        "relationship": "adjacent_competitor",
        "peer": True,
        "graph": "None",
        "note": "Excellent LSP-first agent navigation/editing; low architecture intelligence depth.",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/clines/serena",
    },
    "SonarQube": {
        "category": "sast",
        "status": "Enterprise code quality and security platform",
        "relationship": "adjacent_competitor",
        "peer": True,
        "graph": "CFG/DFG",
        "note": "6,500+ rules with CFG/DFG and taint tracking across 30 languages. Server-based deployment (Docker/cloud).",
        "stars_note": "~10.2k stars is the main SonarQube product; the MCP server extension has ~389 stars.",
        "version_evaluated": "26.2.0",
        "repo_url": "https://github.com/SonarSource/sonarqube",
    },
    "CodeQL": {
        "category": "sast",
        "status": "Dataflow and taint analysis engine",
        "relationship": "adjacent_competitor",
        "peer": True,
        "graph": "CFG/DFG",
        "note": "Full dataflow and taint tracking with a custom query language (QL). GitHub-owned. Security-focused.",
        "version_evaluated": "2.24.2",
        "repo_url": "https://github.com/github/codeql",
    },
    "Semgrep": {
        "category": "sast",
        "status": "Lightweight pattern matching engine",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "Pattern matching",
        "note": "Fast AST-based pattern matching with a community rules marketplace. 3,500+ rules across 30 languages.",
        "version_evaluated": "v1.152.0",
        "repo_url": "https://github.com/semgrep/semgrep",
    },
    "ast-grep": {
        "category": "sast",
        "status": "Pattern-specialized (adjacent)",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "Structural match graph",
        "note": "Excellent structural rule matching and rewriting, no persistent analysis graph.",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/ast-grep/ast-grep",
    },
    "Augment Code": {
        "category": "ide",
        "status": "High distribution pressure",
        "relationship": "direct_competitor",
        "peer": True,
        "graph": "Embedding-centric",
        "note": "Enterprise cloud context engine with strong benchmark claims.",
        "version_evaluated": "N/A",
        "repo_url": "https://augmentcode.com",
    },
    "Cursor": {
        "category": "ide",
        "status": "High distribution pressure",
        "relationship": "adjacent_competitor",
        "peer": True,
        "graph": "Cloud embeddings",
        "note": "Large distribution and codebase indexing, but no deterministic architecture layer.",
        "version_evaluated": "N/A",
        "repo_url": "https://cursor.com",
    },
    "Windsurf": {
        "category": "ide",
        "status": "High distribution pressure",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "Codemaps (LLM)",
        "note": "Strong agent UX and codemap visuals; less deterministic than graph-first analysis.",
        "version_evaluated": "N/A",
        "repo_url": "https://windsurf.com",
    },
    "Claude Code": {
        "category": "agent",
        "status": "Primary integration channel",
        "relationship": "channel_partner_and_competitor",
        "peer": True,
        "graph": "None",
        "note": "Top channel target; lacks persistent index and architecture depth.",
        "version_evaluated": "N/A",
        "repo_url": "https://github.com/anthropics/claude-code",
    },
    "Codex CLI": {
        "category": "agent",
        "status": "Primary integration channel",
        "relationship": "channel_partner_and_competitor",
        "peer": True,
        "graph": "None",
        "note": "Major terminal agent channel; open gap on codebase indexing.",
        "version_evaluated": "N/A",
        "repo_url": "https://github.com/openai/codex",
    },
    "Gemini CLI": {
        "category": "agent",
        "status": "Primary integration channel",
        "relationship": "channel_partner_and_competitor",
        "peer": True,
        "graph": "None",
        "note": "Huge adoption with persistent-index demand that aligns with roam strengths.",
        "version_evaluated": "N/A",
        "repo_url": "https://github.com/google-gemini/gemini-cli",
    },
    "Aider": {
        "category": "agent",
        "status": "High OSS workflow relevance",
        "relationship": "adjacent_competitor",
        "peer": True,
        "graph": "Repo-map PageRank",
        "note": "Uses internal PageRank for map ranking, but not persistent or queryable architecture graph.",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/Aider-AI/aider",
    },
    "Continue.dev": {
        "category": "agent",
        "status": "Workflow-adjacent",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "Workflow/agent orchestration",
        "note": "Strong async-agent workflow and team rules, but not deep architecture graph analysis.",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/continuedev/continue",
    },
    "Sourcegraph/Amp": {
        "category": "code_search",
        "status": "SCIP code search platform",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "SCIP code graph (cloud)",
        "note": "Invented SCIP indexing protocol. Code search + cross-repo navigation. Amp agent spun off Dec 2025.",
        "version_evaluated": "6.12",
        "repo_url": "https://sourcegraph.com/amp",
    },
    "CodePrism": {
        "category": "mcp_server",
        "status": "Graph-based MCP server",
        "relationship": "direct_competitor",
        "peer": True,
        "graph": "Graph-based analysis engine",
        "note": "Rust-based code intelligence MCP server. Universal AST, graph analysis, 20 tools. Local, open source.",
        "version_evaluated": "v0.4.6",
        "repo_url": "https://github.com/rustic-ai/codeprism",
    },
    "Greptile": {
        "category": "code_search",
        "status": "Search-platform adjacent",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "LLM-oriented graph",
        "note": "Cloud-first AI code intelligence; less deterministic architectural metrics.",
        "version_evaluated": "N/A",
        "repo_url": "https://greptile.com",
    },
    "CodeScene": {
        "category": "code_search",
        "status": "Behavioral code analysis platform",
        "relationship": "adjacent_competitor",
        "peer": False,
        "graph": "Behavioral analytics",
        "note": "Git-based behavioral analysis: temporal coupling, team dynamics, code health trends. 15 languages.",
        "version_evaluated": "7.3",
        "repo_url": "https://codescene.com",
    },
    "Repomix": {
        "category": "context",
        "status": "Complementary",
        "relationship": "complementary_tool",
        "peer": False,
        "graph": "None",
        "note": "Context packager with huge adoption; low direct architecture overlap.",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/yamadashy/repomix",
    },
    "CodeGraphMCPServer": {
        "category": "mcp_server",
        "status": "Graph-based code intelligence MCP server",
        "relationship": "direct_competitor",
        "peer": True,
        "graph": "NetworkX (Louvain)",
        "note": "Tree-sitter AST parsing + NetworkX graph analysis. 14 MCP tools. Local, open source.",
        "version_evaluated": "v0.7.3",
        "repo_url": "https://github.com/nahisaho/CodeGraphMCPServer",
    },
    "Context7": {
        "category": "context",
        "status": "Complementary (library docs)",
        "relationship": "complementary_tool",
        "peer": False,
        "graph": "None",
        "note": "Library documentation retrieval MCP. 44k stars. Complementary to roam (docs vs code analysis).",
        "version_evaluated": "HEAD",
        "repo_url": "https://github.com/upstash/context7",
    },
}


def _repo_root() -> Path:
    start = Path(__file__).resolve()
    for parent in [start, *start.parents]:
        tracker = parent / "reports" / "competitor_tracker.md"
        site_dir = parent / "docs" / "site"
        if tracker.exists() and site_dir.exists():
            return parent
    raise RuntimeError("Could not locate repository root from competitor_site_data.py")


def default_tracker_path() -> Path:
    return _repo_root() / "reports" / "competitor_tracker.md"


def default_output_path() -> Path:
    return _repo_root() / "docs" / "site" / "data" / "landscape.json"


def _strip_md(text: str) -> str:
    value = text.strip()
    value = value.replace("\\|", "|")
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*(?!\s)", "", value)
    return value.strip()


def _find_heading(lines: list[str], heading: str) -> int:
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            return idx
    raise ValueError(f"Heading not found: {heading}")


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    if not row.startswith("|"):
        raise ValueError(f"Not a table row: {line!r}")
    return [cell.strip() for cell in row.strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _parse_table_after_heading(lines: list[str], heading: str) -> tuple[list[str], list[dict[str, str]]]:
    idx = _find_heading(lines, heading)
    i = idx + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    if i + 1 >= len(lines):
        raise ValueError(f"Malformed table after heading: {heading}")

    headers = [_strip_md(cell) for cell in _split_table_row(lines[i])]
    sep = _split_table_row(lines[i + 1])
    if not _is_separator_row(sep):
        raise ValueError(f"Missing table separator after heading: {heading}")

    rows: list[dict[str, str]] = []
    j = i + 2
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        cells = _split_table_row(lines[j])
        if _is_separator_row(cells):
            j += 1
            continue
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            head = cells[: len(headers) - 1]
            tail = " | ".join(cells[len(headers) - 1 :])
            cells = head + [tail]
        rows.append(dict(zip(headers, cells)))
        j += 1
    return headers, rows


def _parse_table_after_any_heading(
    lines: list[str],
    headings: list[str],
) -> tuple[str, list[str], list[dict[str, str]]]:
    last_error: Exception | None = None
    for heading in headings:
        try:
            headers, rows = _parse_table_after_heading(lines, heading)
            return heading, headers, rows
        except Exception as exc:  # pragma: no cover - defensive; exercised by fallback behavior
            last_error = exc
            continue
    raise ValueError(f"None of the expected headings were found: {headings}") from last_error


def _parse_yes(value: str) -> bool:
    cleaned = _strip_md(value).lower()
    return cleaned.startswith("yes")


def _normalize_category(raw_category: str, name: str) -> str:
    lowered = raw_category.lower()
    if name == "roam-code":
        return "roam"
    if "mcp server" in lowered:
        return "mcp_server"
    if "ai ide" in lowered:
        return "ide"
    if "ai agent" in lowered or "ide extension" in lowered:
        return "agent"
    if "sast" in lowered or "code quality" in lowered:
        return "sast"
    if "context pack" in lowered:
        return "context"
    if "code search" in lowered or "code intel" in lowered:
        return "code_search"
    return "code_search"


def _decision_entries(
    rows: Iterable[dict[str, str]],
    title_key: str,
    detail_key: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for row in rows:
        title = _strip_md(row.get(title_key, ""))
        detail = _strip_md(row.get(detail_key, ""))
        if title:
            entries.append({"title": title, "detail": detail})
    return entries


def _find_section_line_range(lines: list[str], heading: str) -> tuple[int, int]:
    start = _find_heading(lines, heading) + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return start, end


def _parse_roam_trails(lines: list[str]) -> list[str]:
    start, end = _find_section_line_range(lines, "## Where roam-code Trails")
    items: list[str] = []
    for line in lines[start:end]:
        m = re.match(r"\s*\d+\.\s+(.*)$", line)
        if m:
            items.append(_strip_md(m.group(1)))
    return items


def _parse_matrix_confidence(lines: list[str]) -> dict[str, dict[str, object]]:
    heading = next((line for line in lines if line.startswith("## Matrix Recheck Log")), "")
    if not heading:
        raise ValueError("Heading not found: ## Matrix Recheck Log")
    _, rows = _parse_table_after_heading(lines, heading)
    mapping: dict[str, dict[str, object]] = {}

    alias = {
        "SonarQube MCP": "SonarQube",
        "CodeScene MCP": "CodeScene",
    }
    for row in rows:
        raw_name = _strip_md(row.get("Competitor", ""))
        if not raw_name:
            continue
        name = alias.get(raw_name, raw_name)
        conf = _strip_md(row.get("Confidence", "")) or "Medium"
        source_text = row.get("Source", "")
        source_count = max(
            len(re.findall(r"https?://", source_text)),
            len([part for part in source_text.split(";") if part.strip()]),
        )
        mapping[name] = {
            "confidence": conf,
            "source_count": int(source_count) if source_count > 0 else 1,
        }
    return mapping


def _parse_tracker_updated_iso(tracker_updated: str) -> str:
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", tracker_updated)
    if not m:
        return tracker_updated
    day = int(m.group(1))
    month_name = m.group(2).lower()
    year = int(m.group(3))
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month = month_map.get(month_name)
    if month is None:
        return tracker_updated
    return f"{year:04d}-{month:02d}-{day:02d}"


def _infer_claim_type(*values: str) -> str:
    joined = " ".join(values)
    tokens = joined.lower()
    uncertain = any(marker in tokens for marker in ["~", "n/a", "unknown", "range", "inconsistent", "beta", "client"])
    uncertain = uncertain or bool(re.search(r"\b\d+\s*-\s*\d+\b", joined))
    if uncertain:
        # Mixed when at least one numeric exact-looking token also exists.
        exact_numeric = bool(re.search(r"\b\d+\b", joined))
        return "mixed" if exact_numeric else "estimated"
    return "measured"


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def compute_criterion_score(criterion_def: dict, value: object) -> int:
    """Compute points for a single criterion given its definition and raw value."""
    ctype = criterion_def["type"]
    max_pts = criterion_def["max"]
    if ctype == "binary":
        return max_pts if value else 0
    if ctype == "subjective":
        return min(int(value or 0), max_pts)
    # tiered
    tiers = criterion_def.get("tiers", {})
    if isinstance(tiers, dict):
        return tiers.get(str(value), 0) if value is not None else 0
    # numeric range tiers: [[lo, hi, pts], ...]
    num_val = int(value or 0)
    for entry in tiers:
        lo, hi, pts = entry[0], entry[1], entry[2]
        if hi is None:
            if num_val >= lo:
                return pts
        elif lo <= num_val <= hi:
            return pts
    return 0


def compute_scores(
    criteria_data: dict[str, object],
    rubric: list[dict] | None = None,
) -> dict[str, object]:
    """Compute all scores for a competitor from their criteria data."""
    if rubric is None:
        rubric = SCORING_RUBRIC
    categories: list[dict[str, object]] = []
    subjective_count = 0
    total_criteria = 0
    for cat in rubric:
        criteria_results: list[dict[str, object]] = []
        cat_total = 0
        for crit in cat["criteria"]:
            cid = crit["id"]
            default = False if crit["type"] == "binary" else 0
            raw = criteria_data.get(cid, default)
            pts = compute_criterion_score(crit, raw)
            criteria_results.append({
                "id": cid,
                "label": crit["label"],
                "value": raw,
                "points": pts,
                "max": crit["max"],
                "type": crit["type"],
            })
            cat_total += pts
            total_criteria += 1
            if crit["type"] == "subjective":
                subjective_count += 1
        categories.append({
            "id": cat["id"],
            "label": cat["label"],
            "score": cat_total,
            "max": cat["max_points"],
            "criteria": criteria_results,
        })

    total = sum(c["score"] for c in categories)
    by_id = {c["id"]: c for c in categories}

    sa = by_id.get("static_analysis", {"score": 0, "max": 20})
    gi = by_id.get("graph_intelligence", {"score": 0, "max": 10})
    gt = by_id.get("git_temporal", {"score": 0, "max": 10})
    ai = by_id.get("agent_integration", {"score": 0, "max": 16})
    ea = by_id.get("ecosystem", {"score": 0, "max": 19})

    _safe = lambda n, d: n / d if d else 0.0
    map_y = round(
        (_safe(sa["score"], sa["max"]) * 0.50
         + _safe(gi["score"], gi["max"]) * 0.25
         + _safe(gt["score"], gt["max"]) * 0.25) * 100
    )
    map_x = round(
        (_safe(ai["score"], ai["max"]) * 0.70
         + _safe(ea["score"], ea["max"]) * 0.30) * 100
    )

    return {
        "total": total,
        "max_total": 100,
        "map_x": map_x,
        "map_y": map_y,
        "categories": categories,
        "subjective_count": subjective_count,
        "total_criteria": total_criteria,
    }


# ---------------------------------------------------------------------------
# Matrix → competitor entries
# ---------------------------------------------------------------------------

def _matrix_to_competitors(
    lines: list[str],
    confidence_map: dict[str, dict[str, object]],
    tracker_updated_iso: str,
) -> list[dict[str, object]]:
    headers, rows = _parse_table_after_heading(lines, "## Master Comparison Table (18 Competitors)")
    if not rows:
        raise ValueError("Master comparison table is empty")

    feature_col = headers[0]
    names = headers[1:]
    per_name: dict[str, dict[str, str]] = {name: {} for name in names}
    for row in rows:
        feature = _strip_md(row.get(feature_col, ""))
        if not feature:
            continue
        for name in names:
            per_name[name][feature] = _strip_md(row.get(name, ""))

    competitors: list[dict[str, object]] = []
    missing_metadata: list[str] = []
    for name in names:
        meta = MAP_METADATA.get(name)
        if meta is None:
            missing_metadata.append(name)
            continue

        fields = per_name[name]
        raw_category = fields.get("Category", "")
        mcp_value = fields.get("MCP Tools", "N/A")
        star_value = fields.get("GitHub Stars", "N/A")
        cli_value = fields.get("CLI Commands", "N/A")
        conf_payload = confidence_map.get(name, {})

        criteria = CRITERIA_DATA.get(name, {})
        scores = compute_scores(criteria)

        entry: dict[str, object] = {
            "name": name,
            "category": meta.get("category") or _normalize_category(raw_category, name),
            "category_label": raw_category,
            "stars": star_value,
            "mcp": mcp_value,
            "local": _parse_yes(fields.get("100% Local", "")),
            "cli_commands": cli_value,
            "graph": str(meta["graph"]),
            "arch": scores["map_y"],
            "agent": scores["map_x"],
            "scores": scores,
            "status": str(meta["status"]),
            "relationship": str(meta.get("relationship", "adjacent_competitor")),
            "peer": bool(meta["peer"]),
            "note": str(meta["note"]),
            "confidence": str(conf_payload.get("confidence", "Medium")),
            "source_count": int(conf_payload.get("source_count", 1)),
            "last_verified": tracker_updated_iso,
            "claim_type": _infer_claim_type(mcp_value, star_value, cli_value),
        }
        if meta.get("stars_note"):
            entry["stars_note"] = str(meta["stars_note"])
        if meta.get("mcp_note"):
            entry["mcp_note"] = str(meta["mcp_note"])
        if meta.get("version_evaluated"):
            entry["version_evaluated"] = str(meta["version_evaluated"])
        if meta.get("repo_url"):
            entry["repo_url"] = str(meta["repo_url"])
        competitors.append(entry)

    if missing_metadata:
        raise ValueError(
            "Missing MAP_METADATA entries for competitors: "
            + ", ".join(sorted(missing_metadata))
        )
    return competitors


def _append_ckb_from_leaderboard(
    lines: list[str],
    competitors: list[dict[str, object]],
    confidence_map: dict[str, dict[str, object]],
    tracker_updated_iso: str,
) -> None:
    _, rows = _parse_table_after_heading(lines, "## MCP Tool Count Leaderboard")
    ckb_row = None
    for row in rows:
        if _strip_md(row.get("Tool", "")) == "CKB/CodeMCP":
            ckb_row = row
            break
    if ckb_row is None:
        raise ValueError("CKB/CodeMCP row not found in MCP Tool Count Leaderboard")

    if any(entry["name"] == "CKB/CodeMCP" for entry in competitors):
        return

    meta = MAP_METADATA["CKB/CodeMCP"]
    mcp_value = _strip_md(ckb_row.get("MCP Tools", "N/A"))
    star_value = _strip_md(ckb_row.get("Stars", "N/A"))
    conf_payload = confidence_map.get("CKB/CodeMCP", {})

    criteria = CRITERIA_DATA.get("CKB/CodeMCP", {})
    scores = compute_scores(criteria)

    ckb_entry: dict[str, object] = {
        "name": "CKB/CodeMCP",
        "category": meta["category"],
        "category_label": "MCP Server",
        "stars": star_value,
        "mcp": mcp_value,
        "local": _parse_yes(ckb_row.get("Local?", "")),
        "cli_commands": "N/A",
        "graph": meta["graph"],
        "arch": scores["map_y"],
        "agent": scores["map_x"],
        "scores": scores,
        "status": str(meta["status"]),
        "relationship": str(meta.get("relationship", "direct_competitor")),
        "peer": bool(meta["peer"]),
        "note": str(meta["note"]),
        "confidence": str(conf_payload.get("confidence", "High")),
        "source_count": int(conf_payload.get("source_count", 1)),
        "last_verified": tracker_updated_iso,
        "claim_type": _infer_claim_type(mcp_value, star_value, "N/A"),
    }
    if meta.get("mcp_note"):
        ckb_entry["mcp_note"] = str(meta["mcp_note"])
    if meta.get("version_evaluated"):
        ckb_entry["version_evaluated"] = str(meta["version_evaluated"])
    if meta.get("repo_url"):
        ckb_entry["repo_url"] = str(meta["repo_url"])
    competitors.append(ckb_entry)


def _append_extra_competitors(
    competitors: list[dict[str, object]],
    tracker_updated_iso: str,
) -> None:
    """Add competitors defined in CRITERIA_DATA/MAP_METADATA but not in the tracker markdown."""
    existing_names = {str(c["name"]) for c in competitors}
    for name, meta in MAP_METADATA.items():
        if name in existing_names:
            continue
        criteria = CRITERIA_DATA.get(name)
        if criteria is None:
            continue
        scores = compute_scores(criteria)
        entry: dict[str, object] = {
            "name": name,
            "category": meta.get("category", "code_search"),
            "category_label": str(meta.get("category", "")),
            "stars": str(criteria.get("github_stars", "N/A")),
            "mcp": str(criteria.get("mcp_tools_count", "N/A")),
            "local": bool(criteria.get("local_zero_api", False)),
            "cli_commands": str(criteria.get("cli_commands_count", "N/A")),
            "graph": str(meta.get("graph", "None")),
            "arch": scores["map_y"],
            "agent": scores["map_x"],
            "scores": scores,
            "status": str(meta.get("status", "")),
            "relationship": str(meta.get("relationship", "adjacent_competitor")),
            "peer": bool(meta.get("peer", False)),
            "note": str(meta.get("note", "")),
            "confidence": "Medium",
            "source_count": 1,
            "last_verified": tracker_updated_iso,
            "claim_type": "estimated",
        }
        if meta.get("version_evaluated"):
            entry["version_evaluated"] = str(meta["version_evaluated"])
        if meta.get("repo_url"):
            entry["repo_url"] = str(meta["repo_url"])
        competitors.append(entry)


def build_site_payload(tracker_path: Path | None = None) -> dict[str, object]:
    path = tracker_path or default_tracker_path()
    lines = path.read_text(encoding="utf-8").splitlines()

    tracker_updated = ""
    for line in lines:
        if line.startswith("> Updated:"):
            tracker_updated = line.split(":", 1)[1].strip()
            break
    if not tracker_updated:
        raise ValueError("Could not parse tracker update line ('> Updated: ...').")

    tracker_updated_iso = _parse_tracker_updated_iso(tracker_updated)
    confidence_map = _parse_matrix_confidence(lines)
    competitors = _matrix_to_competitors(lines, confidence_map, tracker_updated_iso)
    _append_ckb_from_leaderboard(lines, competitors, confidence_map, tracker_updated_iso)
    _append_extra_competitors(competitors, tracker_updated_iso)

    # Filter to landscape-included tools only.
    competitors = [c for c in competitors if str(c["name"]) in LANDSCAPE_INCLUDE]

    # Keep visual map stable: order by descending analysis depth, then agent readiness.
    competitors.sort(key=lambda c: (-int(c["arch"]), -int(c["agent"]), str(c["name"])))

    # Keep roam-code row count fields synced to source-of-truth command/tool registration.
    try:
        from roam.surface_counts import collect_surface_counts

        surface = collect_surface_counts()
        cli_counts = surface.get("cli", {})
        mcp_counts = surface.get("mcp", {})
        canonical = int(cli_counts.get("canonical_commands", 0) or 0)
        alias_count = int(cli_counts.get("alias_names", 0) or 0)
        mcp_tools = int(mcp_counts.get("registered_tools", 0) or 0)
        for entry in competitors:
            if entry.get("name") != "roam-code":
                continue
            entry["mcp"] = str(mcp_tools)
            if alias_count > 0:
                entry["cli_commands"] = f"{canonical} canonical (+{alias_count} alias)"
            else:
                entry["cli_commands"] = str(canonical)
            break
    except Exception:
        # Do not fail payload generation if local surface-count parsing fails.
        pass

    methodology = {
        "scoring_model": [
            "45 criteria across 7 categories, scored as binary (has/doesn't), count-tiered, or subjective.",
            "Y-axis (Analysis Depth) = Static Analysis (50%) + Graph Intelligence (25%) + Git Temporal (25%).",
            "X-axis (Agent Readiness) = Agent Integration (70%) + Ecosystem (30%).",
            "Security and Unique Capabilities affect total score only, not map position.",
            "All criteria scored by project maintainers. 44/45 are binary or tiered (count-based or depth-based). 1/45 is subjective (documentation quality).",
            "Weight sliders let viewers apply their own category priorities.",
        ],
        "limitations": [
            "Criteria selection bias: we chose which capabilities to measure, favoring tools with graph algorithms.",
            "Category weight bias: default weights reflect our perspective. Adjust sliders for yours.",
            "Assessment bias: we scored all competitors. Errors or missed capabilities are possible.",
            "Public vendor claims can be stale or inconsistent across pages.",
            "GitHub star counts may conflate main product repos with MCP extension repos.",
        ],
        "reproducibility": {
            "tracker": str(path.relative_to(_repo_root())).replace("\\", "/"),
            "generator": "src/roam/competitor_site_data.py",
            "generated_at_reference": tracker_updated_iso,
        },
    }

    # Rubric metadata for the frontend (weight sliders + methodology rendering)
    rubric_out: list[dict[str, object]] = []
    for cat in SCORING_RUBRIC:
        rubric_out.append({
            "id": cat["id"],
            "label": cat["label"],
            "max_points": cat["max_points"],
            "default_weight": cat["default_weight"],
            "criteria": [
                {k: v for k, v in crit.items()}
                for crit in cat["criteria"]
            ],
        })

    return {
        "tracker_updated": tracker_updated,
        "tracker_updated_iso": tracker_updated_iso,
        "tracker_file": str(path.relative_to(_repo_root())).replace("\\", "/"),
        "competitors": competitors,
        "methodology": methodology,
        "rubric": rubric_out,
    }


def write_site_payload(
    output_path: Path | None = None,
    tracker_path: Path | None = None,
) -> dict[str, object]:
    payload = build_site_payload(tracker_path=tracker_path)
    target = output_path or default_output_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate competitor site data JSON.")
    parser.add_argument(
        "--tracker",
        type=Path,
        default=default_tracker_path(),
        help="Path to reports/competitor_tracker.md",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_output_path(),
        help="Output JSON path (default: docs/site/data/landscape.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; fail if output file differs from generated payload.",
    )
    args = parser.parse_args()

    payload = build_site_payload(tracker_path=args.tracker)
    rendered = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"

    if args.check:
        if not args.out.exists():
            print(f"ERROR: output file does not exist: {args.out}")
            return 1
        existing = args.out.read_text(encoding="utf-8")
        if existing != rendered:
            print(f"ERROR: {args.out} is out of date. Regenerate with:")
            print(f"  python {Path(__file__).as_posix()} --tracker {args.tracker} --out {args.out}")
            return 1
        print(f"OK: {args.out} is in sync.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
