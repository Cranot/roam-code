"""Click CLI entry point with lazy-loaded subcommands."""

from __future__ import annotations

import os
import sys

# Fix Unicode output on Windows consoles (cp1253, cp1252, etc.)
if sys.platform == "win32" and not os.environ.get("PYTHONIOENCODING"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import click


# Lazy-loading command group: imports command modules only when invoked.
# This avoids importing networkx (~500ms) on every CLI call.
# Total: 137 invokable command names (136 canonical commands + 1 legacy alias).
# If this changes, update README.md, CLAUDE.md, llms-install.md, and docs copy.
_COMMANDS = {
    "index":    ("roam.commands.cmd_index",    "index"),
    "map":      ("roam.commands.cmd_map",      "map_cmd"),
    "module":   ("roam.commands.cmd_module",   "module"),
    "file":     ("roam.commands.cmd_file",     "file_cmd"),
    "symbol":   ("roam.commands.cmd_symbol",   "symbol"),
    "trace":    ("roam.commands.cmd_trace",    "trace"),
    "deps":     ("roam.commands.cmd_deps",     "deps"),
    "health":   ("roam.commands.cmd_health",   "health"),
    "clusters": ("roam.commands.cmd_clusters", "clusters"),
    "layers":   ("roam.commands.cmd_layers",   "layers"),
    "weather":  ("roam.commands.cmd_weather",  "weather"),
    "dead":     ("roam.commands.cmd_dead",     "dead"),
    "search":   ("roam.commands.cmd_search",   "search"),
    "grep":     ("roam.commands.cmd_grep",     "grep_cmd"),
    "uses":     ("roam.commands.cmd_uses",     "uses"),
    "impact":   ("roam.commands.cmd_impact",   "impact"),
    "owner":    ("roam.commands.cmd_owner",    "owner"),
    "coupling": ("roam.commands.cmd_coupling", "coupling"),
    "fan":      ("roam.commands.cmd_fan",      "fan"),
    "diff":     ("roam.commands.cmd_diff",     "diff_cmd"),
    "describe": ("roam.commands.cmd_describe", "describe"),
    "test-map": ("roam.commands.cmd_testmap",  "test_map"),
    "sketch":   ("roam.commands.cmd_sketch",   "sketch"),
    "context":     ("roam.commands.cmd_context",     "context"),
    "safe-delete": ("roam.commands.cmd_safe_delete", "safe_delete"),
    "pr-risk":     ("roam.commands.cmd_pr_risk",     "pr_risk"),
    "split":       ("roam.commands.cmd_split",      "split"),
    "risk":        ("roam.commands.cmd_risk",       "risk"),
    "why":         ("roam.commands.cmd_why",        "why"),
    "snapshot":    ("roam.commands.cmd_snapshot",   "snapshot"),
    "trend":       ("roam.commands.cmd_trend",     "trend"),
    "auth-gaps":     ("roam.commands.cmd_auth_gaps",     "auth_gaps_cmd"),
    "coverage-gaps": ("roam.commands.cmd_coverage_gaps", "coverage_gaps"),
    "report":      ("roam.commands.cmd_report",    "report"),
    "understand":  ("roam.commands.cmd_understand", "understand"),
    "onboard":     ("roam.commands.cmd_onboard",    "onboard"),
    "affected-tests": ("roam.commands.cmd_affected_tests", "affected_tests"),
    "complexity":  ("roam.commands.cmd_complexity",  "complexity"),
    "debt":        ("roam.commands.cmd_debt",        "debt"),
    "conventions": ("roam.commands.cmd_conventions", "conventions"),
    "bus-factor":  ("roam.commands.cmd_bus_factor",  "bus_factor"),
    "entry-points": ("roam.commands.cmd_entry_points", "entry_points"),
    "breaking":    ("roam.commands.cmd_breaking",     "breaking"),
    "safe-zones":  ("roam.commands.cmd_safe_zones",  "safe_zones"),
    "doc-staleness": ("roam.commands.cmd_doc_staleness", "doc_staleness"),
    "docs-coverage": ("roam.commands.cmd_docs_coverage", "docs_coverage"),
    "suggest-refactoring": ("roam.commands.cmd_suggest_refactoring", "suggest_refactoring"),
    "plan-refactor": ("roam.commands.cmd_plan_refactor", "plan_refactor"),
    "fn-coupling":  ("roam.commands.cmd_fn_coupling",  "fn_coupling"),
    "alerts":       ("roam.commands.cmd_alerts",       "alerts"),
    "fitness":      ("roam.commands.cmd_fitness",      "fitness"),
    "patterns":     ("roam.commands.cmd_patterns",     "patterns"),
    "preflight":    ("roam.commands.cmd_preflight",    "preflight"),
    "guard":        ("roam.commands.cmd_guard",        "guard"),
    "init":         ("roam.commands.cmd_init",         "init"),
    "config":       ("roam.commands.cmd_config",       "config"),
    "digest":       ("roam.commands.cmd_digest",       "digest"),
    "tour":         ("roam.commands.cmd_tour",         "tour"),
    "diagnose":     ("roam.commands.cmd_diagnose",     "diagnose"),
    "ws":           ("roam.commands.cmd_ws",           "ws"),
    "visualize":    ("roam.commands.cmd_visualize",    "visualize"),
    "x-lang":       ("roam.commands.cmd_xlang",        "xlang"),
    "algo":              ("roam.commands.cmd_math",             "math_cmd"),
    "math":              ("roam.commands.cmd_math",             "math_cmd"),
    "n1":                ("roam.commands.cmd_n1",               "n1_cmd"),
    "minimap":           ("roam.commands.cmd_minimap",          "minimap"),
    "migration-safety":  ("roam.commands.cmd_migration_safety", "migration_safety_cmd"),
    "over-fetch":        ("roam.commands.cmd_over_fetch",       "over_fetch_cmd"),
    "missing-index":     ("roam.commands.cmd_missing_index",    "missing_index_cmd"),
    "orphan-routes":     ("roam.commands.cmd_orphan_routes",    "orphan_routes_cmd"),
    "api-drift":         ("roam.commands.cmd_api_drift",        "api_drift_cmd"),
    "annotate":          ("roam.commands.cmd_annotate",         "annotate"),
    "annotations":       ("roam.commands.cmd_annotate",         "annotations"),
    "dark-matter":       ("roam.commands.cmd_dark_matter",      "dark_matter"),
    "pr-diff":           ("roam.commands.cmd_pr_diff",          "pr_diff"),
    "budget":            ("roam.commands.cmd_budget",           "budget"),
    "effects":           ("roam.commands.cmd_effects",          "effects"),
    "attest":            ("roam.commands.cmd_attest",           "attest"),
    "capsule":           ("roam.commands.cmd_capsule",          "capsule"),
    "path-coverage":     ("roam.commands.cmd_path_coverage",    "path_coverage"),
    "forecast":          ("roam.commands.cmd_forecast",         "forecast"),
    "plan":              ("roam.commands.cmd_plan",             "plan"),
    "adversarial":       ("roam.commands.cmd_adversarial",     "adversarial"),
    "cut":               ("roam.commands.cmd_cut",             "cut"),
    "invariants":        ("roam.commands.cmd_invariants",      "invariants"),
    "bisect":            ("roam.commands.cmd_bisect",          "bisect"),
    "intent":            ("roam.commands.cmd_intent",          "intent"),
    "simulate":          ("roam.commands.cmd_simulate",       "simulate"),
    "closure":           ("roam.commands.cmd_closure",        "closure"),
    "rules":             ("roam.commands.cmd_rules",          "rules"),
    "fingerprint":       ("roam.commands.cmd_fingerprint",   "fingerprint"),
    "spectral":          ("roam.commands.cmd_spectral",       "spectral"),
    "orchestrate":       ("roam.commands.cmd_orchestrate",    "orchestrate"),
    "mutate":            ("roam.commands.cmd_mutate",         "mutate"),
    "vuln-map":          ("roam.commands.cmd_vuln_map",       "vuln_map"),
    "vuln-reach":        ("roam.commands.cmd_vuln_reach",     "vuln_reach"),
    "ingest-trace":      ("roam.commands.cmd_ingest_trace",  "ingest_trace"),
    "hotspots":          ("roam.commands.cmd_hotspots",      "hotspots"),
    "schema":            ("roam.commands.cmd_schema",        "schema_cmd"),
    "search-semantic":   ("roam.commands.cmd_search_semantic", "search_semantic"),
    "relate":            ("roam.commands.cmd_relate",        "relate"),
    "agent-export":      ("roam.commands.cmd_agent_export",  "agent_export"),
    "agent-plan":        ("roam.commands.cmd_agent_plan",    "agent_plan"),
    "agent-context":     ("roam.commands.cmd_agent_context", "agent_context"),
    "syntax-check":      ("roam.commands.cmd_syntax_check",  "syntax_check"),
    "vibe-check":        ("roam.commands.cmd_vibe_check",    "vibe_check"),
    "ai-readiness":      ("roam.commands.cmd_ai_readiness",  "ai_readiness"),
    "check-rules":       ("roam.commands.cmd_check_rules",  "check_rules"),
    "codeowners":        ("roam.commands.cmd_codeowners",    "codeowners"),
    "dashboard":         ("roam.commands.cmd_dashboard",     "dashboard"),
    "drift":             ("roam.commands.cmd_drift",         "drift"),
    "dev-profile":       ("roam.commands.cmd_dev_profile",   "dev_profile"),
    "secrets":           ("roam.commands.cmd_secrets",       "secrets"),
    "supply-chain":     ("roam.commands.cmd_supply_chain", "supply_chain"),
    "simulate-departure": ("roam.commands.cmd_simulate_departure", "simulate_departure"),
    "suggest-reviewers": ("roam.commands.cmd_suggest_reviewers", "suggest_reviewers"),
    "verify":            ("roam.commands.cmd_verify",        "verify"),
    "api-changes":       ("roam.commands.cmd_api_changes",   "api_changes"),
    "test-gaps":         ("roam.commands.cmd_test_gaps",     "test_gaps"),
    "ai-ratio":          ("roam.commands.cmd_ai_ratio",      "ai_ratio"),
    "duplicates":        ("roam.commands.cmd_duplicates",    "duplicates"),
    "partition":         ("roam.commands.cmd_partition",     "partition"),
    "affected":          ("roam.commands.cmd_affected",      "affected"),
    "semantic-diff":     ("roam.commands.cmd_semantic_diff", "semantic_diff"),
    "trends":            ("roam.commands.cmd_trends",        "trends"),
    "endpoints":         ("roam.commands.cmd_endpoints",     "endpoints"),
    "watch":             ("roam.commands.cmd_watch",         "watch"),
    "mcp":               ("roam.mcp_server",                 "mcp_cmd"),
    "doctor":            ("roam.commands.cmd_doctor",        "doctor"),
    "reset":             ("roam.commands.cmd_reset",         "reset"),
    "clean":             ("roam.commands.cmd_clean",         "clean"),
    "hooks":             ("roam.commands.cmd_hooks",         "hooks"),
    "smells":            ("roam.commands.cmd_smells",        "smells"),
    "mcp-setup":         ("roam.commands.cmd_mcp_setup",    "mcp_setup"),
    "verify-imports":    ("roam.commands.cmd_verify_imports", "verify_imports_cmd"),
    "vulns":             ("roam.commands.cmd_vulns",         "vulns"),
    "metrics":           ("roam.commands.cmd_metrics",       "metrics"),
}

# Command categories for organized --help display
_CATEGORIES = {
    "Getting Started": ["index", "watch", "init", "hooks", "reset", "clean", "config", "doctor", "understand", "onboard", "dashboard", "tour", "describe", "minimap", "agent-export", "ws", "schema", "mcp", "mcp-setup"],
    "Daily Workflow": ["preflight", "guard", "agent-plan", "agent-context", "pr-risk", "pr-diff", "api-changes", "semantic-diff", "test-gaps", "affected", "attest", "adversarial", "verify", "verify-imports", "diff", "context", "affected-tests", "diagnose", "digest", "annotate", "annotations", "plan", "syntax-check"],
    "Codebase Health": ["health", "smells", "vibe-check", "ai-readiness", "check-rules", "ai-ratio", "trends", "weather", "debt", "complexity", "algo", "n1", "over-fetch", "missing-index", "alerts", "trend", "fitness", "snapshot", "forecast", "bisect", "ingest-trace", "hotspots"],
    "Architecture": ["map", "layers", "clusters", "spectral", "coupling", "dark-matter", "effects", "cut", "simulate", "orchestrate", "partition", "entry-points", "patterns", "safe-zones", "visualize", "x-lang", "fingerprint"],
    "Exploration": ["search", "search-semantic", "grep", "file", "symbol", "module", "trace", "deps", "uses", "fan", "impact", "relate", "endpoints", "metrics"],
    "Reports & CI": ["report", "budget", "breaking", "coverage-gaps", "auth-gaps", "orphan-routes", "bus-factor", "simulate-departure", "suggest-reviewers", "dev-profile", "owner", "codeowners", "drift", "secrets", "supply-chain", "risk", "migration-safety", "api-drift", "path-coverage", "capsule", "rules", "vuln-map", "vuln-reach", "vulns"],
    "Refactoring": ["dead", "duplicates", "safe-delete", "split", "fn-coupling", "doc-staleness", "docs-coverage", "suggest-refactoring", "plan-refactor", "conventions", "sketch", "test-map", "why", "pr-risk", "invariants", "intent", "closure", "mutate"],
}

_PLUGIN_COMMANDS_LOADED = False


def _ensure_plugin_commands_loaded() -> None:
    """Merge discovered plugin commands into the CLI command map once."""
    global _PLUGIN_COMMANDS_LOADED
    if _PLUGIN_COMMANDS_LOADED:
        return
    _PLUGIN_COMMANDS_LOADED = True

    try:
        from roam.plugins import get_plugin_commands

        for cmd_name, target in get_plugin_commands().items():
            if cmd_name in _COMMANDS:
                continue
            _COMMANDS[cmd_name] = target
    except Exception:
        # Plugin loading should never break core CLI behavior.
        return


class LazyGroup(click.Group):
    """A Click group that lazy-loads command modules on first access."""

    def list_commands(self, ctx):
        _ensure_plugin_commands_loaded()
        return sorted(_COMMANDS.keys())

    def get_command(self, ctx, cmd_name):
        _ensure_plugin_commands_loaded()
        if cmd_name not in _COMMANDS:
            return None
        module_path, attr_name = _COMMANDS[cmd_name]
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr_name)

    def invoke(self, ctx):
        """Override invoke to map unhandled exceptions to standardized exit codes.

        RoamError subclasses (IndexMissingError, GateFailureError, etc.) carry
        their own exit_code and are handled by Click's ClickException machinery.
        This override catches *unexpected* exceptions (KeyError, TypeError, etc.)
        and maps them to EXIT_ERROR (1) instead of letting Python print a traceback
        with exit code 1 (which is ambiguous).
        """
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit:
            # click.Context.exit() raises this — propagate as-is
            raise
        except (click.Abort, click.ClickException, SystemExit):
            # Click-managed exceptions — propagate as-is
            raise
        except Exception as exc:
            from roam.exit_codes import EXIT_ERROR
            click.echo(f"Error: {exc}", err=True)
            ctx.exit(EXIT_ERROR)

    def format_help(self, ctx, formatter):
        """Categorized help display instead of flat alphabetical list."""
        _ensure_plugin_commands_loaded()
        self.format_usage(ctx, formatter)
        formatter.write("\n")
        if self.help:
            formatter.write(self.help + "\n\n")

        # Show categorized commands (first 4 categories = ~20 commands)
        shown = set()
        priority_cats = ["Getting Started", "Daily Workflow", "Codebase Health", "Architecture"]
        for cat_name in priority_cats:
            cmds = _CATEGORIES.get(cat_name, [])
            valid_cmds = [c for c in cmds if c in _COMMANDS and c not in shown]
            if not valid_cmds:
                continue
            formatter.write(f"  {cat_name}:\n")
            for cmd_name in valid_cmds:
                cmd = self.get_command(ctx, cmd_name)
                if cmd is None:
                    continue
                help_text = cmd.get_short_help_str(limit=60) if cmd else ""
                formatter.write(f"    {cmd_name:20s} {help_text}\n")
                shown.add(cmd_name)
            formatter.write("\n")

        remaining = sorted(c for c in _COMMANDS if c not in shown)
        if remaining:
            formatter.write(f"  More Commands ({len(remaining)}):\n")
            formatter.write(f"    {', '.join(remaining)}\n\n")

        formatter.write("  Run `roam <command> --help` for details on any command.\n")


def _run_check(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback for --check: run critical install checks and exit.

    Validates the five minimum requirements for roam-code to function:
      1. Python >= 3.9
      2. tree-sitter importable
      3. tree-sitter-language-pack importable
      4. git on PATH
      5. SQLite in-memory DB usable

    Exits 0 on success ("roam-code ready"), 1 on any failure.
    """
    if not value or ctx.resilient_parsing:
        return

    issues: list[str] = []

    # 1. Python version
    if sys.version_info < (3, 9):
        issues.append(
            f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.9"
        )

    # 2. tree-sitter
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        issues.append("tree-sitter not installed")

    # 3. tree-sitter-language-pack
    try:
        import tree_sitter_language_pack  # noqa: F401
    except ImportError:
        issues.append("tree-sitter-language-pack not installed")

    # 4. git on PATH
    import shutil
    if not shutil.which("git"):
        issues.append("git not found in PATH")

    # 5. SQLite in-memory database
    try:
        import sqlite3
        _conn = sqlite3.connect(":memory:")
        _conn.execute("SELECT 1")
        _conn.close()
    except Exception as exc:  # pragma: no cover
        issues.append(f"SQLite error: {exc}")

    if issues:
        click.echo(f"roam-code setup incomplete: {'; '.join(issues)}")
        ctx.exit(1)
    else:
        click.echo("roam-code ready")
        ctx.exit(0)


def _check_gate(gate_expr: str, data: dict) -> bool:
    """Evaluate a gate expression like 'score>=70' against data.

    Returns True if the gate passes, False if it fails.
    Supports: key>=N, key<=N, key>N, key<N, key=N
    """
    import re
    m = re.match(r'^(\w+)\s*(>=|<=|>|<|=)\s*(\d+(?:\.\d+)?)$', gate_expr.strip())
    if not m:
        return True  # can't parse, pass by default
    key, op, val_str = m.groups()
    val = float(val_str)

    actual = data.get(key)
    if actual is None:
        return True  # key not found, pass

    actual = float(actual)
    if op == '>=': return actual >= val
    if op == '<=': return actual <= val
    if op == '>': return actual > val
    if op == '<': return actual < val
    if op == '=': return actual == val
    return True


@click.group(cls=LazyGroup)
@click.version_option(package_name="roam-code")
@click.option(
    '--check',
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_run_check,
    help='Quick setup verification: checks Python, tree-sitter, git, SQLite',
)
@click.option('--json', 'json_mode', is_flag=True, help='Output in JSON format')
@click.option('--compact', is_flag=True, help='Compact output: TSV tables, minimal JSON envelope')
@click.option('--agent', is_flag=True, help='Agent mode: compact JSON with 500-token default budget')
@click.option('--sarif', 'sarif_mode', is_flag=True, help='Output in SARIF 2.1.0 format (for dead, health, complexity, rules, secrets, algo)')
@click.option('--budget', type=int, default=0, help='Max output tokens (0=unlimited)')
@click.option('--include-excluded', is_flag=True, help='Include files normally excluded by .roamignore / config / built-in patterns')
@click.option('--detail', is_flag=True, help='Show full detailed output instead of compact summary')
@click.pass_context
def cli(ctx, json_mode, compact, agent, sarif_mode, budget, include_excluded, detail):
    """Roam: Codebase comprehension tool."""
    if agent and sarif_mode:
        raise click.UsageError("--agent cannot be combined with --sarif")

    # Agent mode is optimized for CLI-invoked sub-agents:
    # - forces JSON for machine parsing
    # - uses compact envelope to reduce token overhead
    # - defaults to 500-token budget unless user overrides with --budget
    if agent:
        json_mode = True
        compact = True
        if budget <= 0:
            budget = 500

    ctx.ensure_object(dict)
    ctx.obj['json'] = json_mode
    ctx.obj['compact'] = compact
    ctx.obj['agent'] = agent
    ctx.obj['sarif'] = sarif_mode
    ctx.obj['budget'] = budget
    ctx.obj['include_excluded'] = include_excluded
    ctx.obj['detail'] = detail
