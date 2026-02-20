"""Click CLI entry point with lazy-loaded subcommands."""

import os
import sys

# Fix Unicode output on Windows consoles (cp1253, cp1252, etc.)
if sys.platform == "win32" and not os.environ.get("PYTHONIOENCODING"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import click


# Lazy-loading command group: imports command modules only when invoked.
# This avoids importing networkx (~500ms) on every CLI call.
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
    "affected-tests": ("roam.commands.cmd_affected_tests", "affected_tests"),
    "complexity":  ("roam.commands.cmd_complexity",  "complexity"),
    "debt":        ("roam.commands.cmd_debt",        "debt"),
    "conventions": ("roam.commands.cmd_conventions", "conventions"),
    "bus-factor":  ("roam.commands.cmd_bus_factor",  "bus_factor"),
    "entry-points": ("roam.commands.cmd_entry_points", "entry_points"),
    "breaking":    ("roam.commands.cmd_breaking",     "breaking"),
    "safe-zones":  ("roam.commands.cmd_safe_zones",  "safe_zones"),
    "doc-staleness": ("roam.commands.cmd_doc_staleness", "doc_staleness"),
    "fn-coupling":  ("roam.commands.cmd_fn_coupling",  "fn_coupling"),
    "alerts":       ("roam.commands.cmd_alerts",       "alerts"),
    "fitness":      ("roam.commands.cmd_fitness",      "fitness"),
    "patterns":     ("roam.commands.cmd_patterns",     "patterns"),
    "preflight":    ("roam.commands.cmd_preflight",    "preflight"),
    "init":         ("roam.commands.cmd_init",         "init"),
    "config":       ("roam.commands.cmd_config",       "config"),
    "digest":       ("roam.commands.cmd_digest",       "digest"),
    "tour":         ("roam.commands.cmd_tour",         "tour"),
    "diagnose":     ("roam.commands.cmd_diagnose",     "diagnose"),
    "ws":           ("roam.commands.cmd_ws",           "ws"),
    "visualize":    ("roam.commands.cmd_visualize",    "visualize"),
    "x-lang":       ("roam.commands.cmd_xlang",        "xlang"),
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
}

# Command categories for organized --help display
_CATEGORIES = {
    "Getting Started": ["index", "init", "config", "understand", "tour", "describe", "minimap", "ws"],
    "Daily Workflow": ["preflight", "pr-risk", "pr-diff", "diff", "context", "affected-tests", "diagnose", "digest", "annotate", "annotations"],
    "Codebase Health": ["health", "weather", "debt", "complexity", "math", "n1", "over-fetch", "missing-index", "alerts", "trend", "fitness", "snapshot"],
    "Architecture": ["map", "layers", "clusters", "coupling", "dark-matter", "entry-points", "patterns", "safe-zones", "visualize", "x-lang"],
    "Exploration": ["search", "grep", "file", "symbol", "module", "trace", "deps", "uses", "fan", "impact"],
    "Reports & CI": ["report", "budget", "breaking", "coverage-gaps", "auth-gaps", "orphan-routes", "bus-factor", "owner", "risk", "migration-safety", "api-drift"],
    "Refactoring": ["dead", "safe-delete", "split", "fn-coupling", "doc-staleness", "conventions", "sketch", "test-map", "why", "pr-risk"],
}


class LazyGroup(click.Group):
    """A Click group that lazy-loads command modules on first access."""

    def list_commands(self, ctx):
        return sorted(_COMMANDS.keys())

    def get_command(self, ctx, cmd_name):
        if cmd_name not in _COMMANDS:
            return None
        module_path, attr_name = _COMMANDS[cmd_name]
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr_name)

    def format_help(self, ctx, formatter):
        """Categorized help display instead of flat alphabetical list."""
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
@click.option('--json', 'json_mode', is_flag=True, help='Output in JSON format')
@click.option('--compact', is_flag=True, help='Compact output: TSV tables, minimal JSON envelope')
@click.pass_context
def cli(ctx, json_mode, compact):
    """Roam: Codebase comprehension tool."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = json_mode
    ctx.obj['compact'] = compact
