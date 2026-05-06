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
# Total: 158 invokable command names (155 canonical commands + 3 alias names).
# If this changes, update README.md, CLAUDE.md, llms-install.md, and docs copy.
# redacteddeprecated commands map to their replacement.  When a user
# invokes a deprecated name, we still resolve it (no breaking change)
# but print a note on stderr suggesting the replacement.
_DEPRECATED_COMMANDS: dict[str, str] = {}

_COMMANDS = {
    "index": ("roam.commands.cmd_index", "index"),
    "map": ("roam.commands.cmd_map", "map_cmd"),
    "module": ("roam.commands.cmd_module", "module"),
    "file": ("roam.commands.cmd_file", "file_cmd"),
    "symbol": ("roam.commands.cmd_symbol", "symbol"),
    "trace": ("roam.commands.cmd_trace", "trace"),
    "deps": ("roam.commands.cmd_deps", "deps"),
    "health": ("roam.commands.cmd_health", "health"),
    "clusters": ("roam.commands.cmd_clusters", "clusters"),
    "layers": ("roam.commands.cmd_layers", "layers"),
    "weather": ("roam.commands.cmd_weather", "weather"),
    "churn": ("roam.commands.cmd_weather", "weather"),
    "dead": ("roam.commands.cmd_dead", "dead"),
    "search": ("roam.commands.cmd_search", "search"),
    "grep": ("roam.commands.cmd_grep", "grep_cmd"),
    "uses": ("roam.commands.cmd_uses", "uses"),
    # Phase-1.5 — ``refs`` is a grep-familiar alias for ``uses``. Agents
    # reaching for "find references to X" hit this name first; the real
    # work happens in cmd_uses through the indexed call/import graph
    # (no string-literal / comment false positives).
    "refs": ("roam.commands.cmd_uses", "uses"),
    "impact": ("roam.commands.cmd_impact", "impact"),
    "owner": ("roam.commands.cmd_owner", "owner"),
    "coupling": ("roam.commands.cmd_coupling", "coupling"),
    "fan": ("roam.commands.cmd_fan", "fan"),
    "diff": ("roam.commands.cmd_diff", "diff_cmd"),
    "describe": ("roam.commands.cmd_describe", "describe"),
    "test-map": ("roam.commands.cmd_testmap", "test_map"),
    "sketch": ("roam.commands.cmd_sketch", "sketch"),
    "context": ("roam.commands.cmd_context", "context"),
    "safe-delete": ("roam.commands.cmd_safe_delete", "safe_delete"),
    "pr-risk": ("roam.commands.cmd_pr_risk", "pr_risk"),
    "split": ("roam.commands.cmd_split", "split"),
    "risk": ("roam.commands.cmd_risk", "risk"),
    "why": ("roam.commands.cmd_why", "why"),
    "auth-gaps": ("roam.commands.cmd_auth_gaps", "auth_gaps_cmd"),
    "coverage-gaps": ("roam.commands.cmd_coverage_gaps", "coverage_gaps"),
    "report": ("roam.commands.cmd_report", "report"),
    "understand": ("roam.commands.cmd_understand", "understand"),
    "onboard": ("roam.commands.cmd_understand", "understand"),
    "affected-tests": ("roam.commands.cmd_affected_tests", "affected_tests"),
    "complexity": ("roam.commands.cmd_complexity", "complexity"),
    "py-types": ("roam.commands.cmd_py_types", "py_types"),
    "py-modern": ("roam.commands.cmd_py_modern", "py_modern"),
    "pytest-fixtures": ("roam.commands.cmd_pytest_fixtures", "pytest_fixtures"),
    "hover": ("roam.commands.cmd_hover", "hover"),
    "debt": ("roam.commands.cmd_debt", "debt"),
    "conventions": ("roam.commands.cmd_conventions", "conventions"),
    "bus-factor": ("roam.commands.cmd_bus_factor", "bus_factor"),
    "entry-points": ("roam.commands.cmd_entry_points", "entry_points"),
    "breaking": ("roam.commands.cmd_breaking", "breaking"),
    "safe-zones": ("roam.commands.cmd_safe_zones", "safe_zones"),
    "doc-staleness": ("roam.commands.cmd_doc_staleness", "doc_staleness"),
    "docs-coverage": ("roam.commands.cmd_docs_coverage", "docs_coverage"),
    "suggest-refactoring": ("roam.commands.cmd_suggest_refactoring", "suggest_refactoring"),
    "plan-refactor": ("roam.commands.cmd_plan_refactor", "plan_refactor"),
    "fn-coupling": ("roam.commands.cmd_fn_coupling", "fn_coupling"),
    "alerts": ("roam.commands.cmd_alerts", "alerts"),
    "fitness": ("roam.commands.cmd_fitness", "fitness"),
    "patterns": ("roam.commands.cmd_patterns", "patterns"),
    "preflight": ("roam.commands.cmd_preflight", "preflight"),
    "permit": ("roam.commands.cmd_permit", "permit_cmd"),
    "postmortem": ("roam.commands.cmd_postmortem", "postmortem_cmd"),
    "article-12-check": ("roam.commands.cmd_article_12_check", "article_12_check_cmd"),
    "capabilities": ("roam.commands.cmd_capabilities", "capabilities_cmd"),
    "guard": ("roam.commands.cmd_guard", "guard"),
    "init": ("roam.commands.cmd_init", "init"),
    "config": ("roam.commands.cmd_config", "config"),
    "tour": ("roam.commands.cmd_tour", "tour"),
    "diagnose": ("roam.commands.cmd_diagnose", "diagnose"),
    "ws": ("roam.commands.cmd_ws", "ws"),
    "visualize": ("roam.commands.cmd_visualize", "visualize"),
    "x-lang": ("roam.commands.cmd_xlang", "xlang"),
    "algo": ("roam.commands.cmd_math", "math_cmd"),
    "math": ("roam.commands.cmd_math", "math_cmd"),
    "n1": ("roam.commands.cmd_n1", "n1_cmd"),
    "minimap": ("roam.commands.cmd_minimap", "minimap"),
    "migration-safety": ("roam.commands.cmd_migration_safety", "migration_safety_cmd"),
    "over-fetch": ("roam.commands.cmd_over_fetch", "over_fetch_cmd"),
    "missing-index": ("roam.commands.cmd_missing_index", "missing_index_cmd"),
    "orphan-routes": ("roam.commands.cmd_orphan_routes", "orphan_routes_cmd"),
    "api-drift": ("roam.commands.cmd_api_drift", "api_drift_cmd"),
    "annotate": ("roam.commands.cmd_annotate", "annotate"),
    "annotations": ("roam.commands.cmd_annotate", "annotations"),
    "dark-matter": ("roam.commands.cmd_dark_matter", "dark_matter"),
    "pr-diff": ("roam.commands.cmd_pr_diff", "pr_diff"),
    "budget": ("roam.commands.cmd_budget", "budget"),
    "effects": ("roam.commands.cmd_effects", "effects"),
    "attest": ("roam.commands.cmd_attest", "attest"),
    "capsule": ("roam.commands.cmd_capsule", "capsule"),
    "path-coverage": ("roam.commands.cmd_path_coverage", "path_coverage"),
    "plugins": ("roam.commands.cmd_plugins", "plugins_cmd"),
    "test-pyramid": ("roam.commands.cmd_test_pyramid", "test_pyramid"),
    "index-stats": ("roam.commands.cmd_index_stats", "index_stats"),
    "telemetry": ("roam.commands.cmd_telemetry", "telemetry"),
    "orphan-imports": ("roam.commands.cmd_orphan_imports", "orphan_imports"),
    "changelog": ("roam.commands.cmd_changelog", "changelog"),
    "graph-export": ("roam.commands.cmd_graph_export", "graph_export"),
    "graph-stats": ("roam.commands.cmd_graph_stats", "graph_stats"),
    "help-search": ("roam.commands.cmd_help_search", "help_search"),
    "timeline": ("roam.commands.cmd_timeline", "timeline"),
    "pr-prep": ("roam.commands.cmd_pr_prep", "pr_prep"),
    "pr-analyze": ("roam.commands.cmd_pr_analyze", "pr_analyze"),
    "pr-comment-render": ("roam.commands.cmd_pr_comment_render", "pr_comment_render"),
    "metrics-push": ("roam.commands.cmd_metrics_push", "metrics_push"),
    "audit-trail-verify": ("roam.commands.cmd_audit_trail_verify", "audit_trail_verify"),
    "audit-trail-export": ("roam.commands.cmd_audit_trail_export", "audit_trail_export"),
    "audit-trail-conformance-check": (
        "roam.commands.cmd_audit_trail_conformance",
        "audit_trail_conformance_check",
    ),
    "rules-validate": ("roam.commands.cmd_rules_validate", "rules_validate"),
    "dogfood": ("roam.commands.cmd_dogfood", "dogfood"),
    "suppress": ("roam.commands.cmd_suppress", "suppress"),
    "stats": ("roam.commands.cmd_stats", "stats"),
    "why-fail": ("roam.commands.cmd_why_fail", "why_fail"),
    "recommend": ("roam.commands.cmd_recommend", "recommend"),
    "api": ("roam.commands.cmd_api", "api"),
    "exit-codes": ("roam.commands.cmd_exit_codes", "exit_codes"),
    "version": ("roam.commands.cmd_version", "version"),
    "disambiguate": ("roam.commands.cmd_disambiguate", "disambiguate"),
    "audit": ("roam.commands.cmd_audit", "audit"),
    "pre-commit": ("roam.commands.cmd_pre_commit", "pre_commit"),
    "mcp-status": ("roam.commands.cmd_mcp_status", "mcp_status"),
    "test-impact": ("roam.commands.cmd_test_impact", "test_impact"),
    "recipes": ("roam.commands.cmd_recipes", "recipes"),
    "forecast": ("roam.commands.cmd_forecast", "forecast"),
    "plan": ("roam.commands.cmd_plan", "plan"),
    "adversarial": ("roam.commands.cmd_adversarial", "adversarial"),
    "cut": ("roam.commands.cmd_cut", "cut"),
    "invariants": ("roam.commands.cmd_invariants", "invariants"),
    "bisect": ("roam.commands.cmd_bisect", "bisect"),
    "intent": ("roam.commands.cmd_intent", "intent"),
    "simulate": ("roam.commands.cmd_simulate", "simulate"),
    "closure": ("roam.commands.cmd_closure", "closure"),
    "rules": ("roam.commands.cmd_rules", "rules"),
    "fingerprint": ("roam.commands.cmd_fingerprint", "fingerprint"),
    "spectral": ("roam.commands.cmd_spectral", "spectral"),
    "orchestrate": ("roam.commands.cmd_orchestrate", "orchestrate"),
    "mutate": ("roam.commands.cmd_mutate", "mutate"),
    "vuln-map": ("roam.commands.cmd_vuln_map", "vuln_map"),
    "vuln-reach": ("roam.commands.cmd_vuln_reach", "vuln_reach"),
    "ingest-trace": ("roam.commands.cmd_ingest_trace", "ingest_trace"),
    "hotspots": ("roam.commands.cmd_hotspots", "hotspots"),
    "schema": ("roam.commands.cmd_schema", "schema_cmd"),
    "search-semantic": ("roam.commands.cmd_search_semantic", "search_semantic"),
    "relate": ("roam.commands.cmd_relate", "relate"),
    "agent-export": ("roam.commands.cmd_agent_export", "agent_export"),
    "agent-plan": ("roam.commands.cmd_agent_plan", "agent_plan"),
    "agent-context": ("roam.commands.cmd_agent_context", "agent_context"),
    "syntax-check": ("roam.commands.cmd_syntax_check", "syntax_check"),
    "vibe-check": ("roam.commands.cmd_vibe_check", "vibe_check"),
    "ai-readiness": ("roam.commands.cmd_ai_readiness", "ai_readiness"),
    "check-rules": ("roam.commands.cmd_check_rules", "check_rules"),
    "codeowners": ("roam.commands.cmd_codeowners", "codeowners"),
    "dashboard": ("roam.commands.cmd_dashboard", "dashboard"),
    "drift": ("roam.commands.cmd_drift", "drift"),
    "dev-profile": ("roam.commands.cmd_dev_profile", "dev_profile"),
    "secrets": ("roam.commands.cmd_secrets", "secrets"),
    "supply-chain": ("roam.commands.cmd_supply_chain", "supply_chain"),
    "simulate-departure": ("roam.commands.cmd_simulate_departure", "simulate_departure"),
    "suggest-reviewers": ("roam.commands.cmd_suggest_reviewers", "suggest_reviewers"),
    "verify": ("roam.commands.cmd_verify", "verify"),
    "api-changes": ("roam.commands.cmd_api_changes", "api_changes"),
    "test-gaps": ("roam.commands.cmd_test_gaps", "test_gaps"),
    "ai-ratio": ("roam.commands.cmd_ai_ratio", "ai_ratio"),
    "duplicates": ("roam.commands.cmd_duplicates", "duplicates"),
    "partition": ("roam.commands.cmd_partition", "partition"),
    "affected": ("roam.commands.cmd_affected", "affected"),
    "semantic-diff": ("roam.commands.cmd_semantic_diff", "semantic_diff"),
    "trends": ("roam.commands.cmd_trends", "trends"),
    # Aliases for the consolidated trends command redacted). Older
    # docs and agent recipes still mention `roam trend` / `roam digest`;
    # we keep them as discoverable aliases instead of breaking the
    # documented surface.
    "trend": ("roam.commands.cmd_trends", "trends"),
    "digest": ("roam.commands.cmd_trends", "trends"),
    "snapshot": ("roam.commands.cmd_trends", "trends"),
    "endpoints": ("roam.commands.cmd_endpoints", "endpoints"),
    "watch": ("roam.commands.cmd_watch", "watch"),
    "mcp": ("roam.mcp_server", "mcp_cmd"),
    "doctor": ("roam.commands.cmd_doctor", "doctor"),
    "reset": ("roam.commands.cmd_reset", "reset"),
    "clean": ("roam.commands.cmd_clean", "clean"),
    "hooks": ("roam.commands.cmd_hooks", "hooks"),
    "smells": ("roam.commands.cmd_smells", "smells"),
    "mcp-setup": ("roam.commands.cmd_mcp_setup", "mcp_setup"),
    "verify-imports": ("roam.commands.cmd_verify_imports", "verify_imports_cmd"),
    "vulns": ("roam.commands.cmd_vulns", "vulns"),
    "metrics": ("roam.commands.cmd_metrics", "metrics"),
    "congestion": ("roam.commands.cmd_congestion", "congestion"),
    "adrs": ("roam.commands.cmd_adrs", "adrs"),
    "flag-dead": ("roam.commands.cmd_flag_dead", "flag_dead"),
    "test-scaffold": ("roam.commands.cmd_test_scaffold", "test_scaffold"),
    "sbom": ("roam.commands.cmd_sbom", "sbom"),
    "triage": ("roam.commands.cmd_triage", "triage"),
    "ci-setup": ("roam.commands.cmd_ci_setup", "ci_setup"),
    "clones": ("roam.commands.cmd_clones", "clones"),
    "retrieve": ("roam.commands.cmd_retrieve", "retrieve"),
    "critique": ("roam.commands.cmd_critique", "critique"),
    "fleet": ("roam.commands.cmd_fleet", "fleet"),
    "ask": ("roam.commands.cmd_ask", "ask"),
    "workflow": ("roam.commands.cmd_workflow", "workflow"),
    "taint": ("roam.commands.cmd_taint", "taint"),
    "cga": ("roam.commands.cmd_cga", "cga"),
    "eval-retrieve": ("roam.commands.cmd_eval_retrieve", "eval_retrieve"),
    "oracle": ("roam.commands.cmd_oracle", "oracle"),
    "index-export": ("roam.commands.cmd_index_bundle", "index_export"),
    "index-import": ("roam.commands.cmd_index_bundle", "index_import"),
}

# Command categories for organized --help display
_CATEGORIES = {
    "Getting Started": [
        "ask",
        "workflow",
        "index",
        "index-export",
        "index-import",
        "watch",
        "init",
        "hooks",
        "reset",
        "clean",
        "config",
        "doctor",
        "understand",
        "onboard",
        "dashboard",
        "tour",
        "describe",
        "minimap",
        "agent-export",
        "ws",
        "schema",
        "mcp",
        "mcp-setup",
        "mcp-status",
        "ci-setup",
        "adrs",
        "audit",
        "changelog",
        "exit-codes",
        "help-search",
        "plugins",
        "pre-commit",
        "recipes",
        "version",
        "index-stats",
        "stats",
        "telemetry",
    ],
    "Daily Workflow": [
        "preflight",
        "permit",
        "postmortem",
        "guard",
        "agent-plan",
        "agent-context",
        "pr-risk",
        "pr-prep",
        "pr-analyze",
        "pr-comment-render",
        "rules-validate",
        "metrics-push",
        "audit-trail-verify",
        "audit-trail-export",
        "audit-trail-conformance-check",
        "article-12-check",
        "capabilities",
        "dogfood",
        "suppress",
        "pr-diff",
        "api-changes",
        "semantic-diff",
        "test-gaps",
        "affected",
        "attest",
        "adversarial",
        "verify",
        "verify-imports",
        "diff",
        "context",
        "hover",
        "retrieve",
        "critique",
        "fleet",
        "affected-tests",
        "test-impact",
        "diagnose",
        "why-fail",
        "recommend",
        "api",
        "disambiguate",
        "annotate",
        "annotations",
        "plan",
        "syntax-check",
        "triage",
        "oracle",
    ],
    "Codebase Health": [
        "health",
        "smells",
        "vibe-check",
        "ai-readiness",
        "check-rules",
        "ai-ratio",
        "trends",
        "weather",
        "churn",
        "timeline",
        "debt",
        "complexity",
        "py-types",
        "py-modern",
        "pytest-fixtures",
        "algo",
        "n1",
        "over-fetch",
        "missing-index",
        "alerts",
        "fitness",
        "forecast",
        "bisect",
        "ingest-trace",
        "hotspots",
        "eval-retrieve",
    ],
    "Architecture": [
        "map",
        "graph-export",
        "graph-stats",
        "layers",
        "clusters",
        "spectral",
        "coupling",
        "dark-matter",
        "effects",
        "cut",
        "simulate",
        "orchestrate",
        "partition",
        "entry-points",
        "patterns",
        "safe-zones",
        "visualize",
        "x-lang",
        "fingerprint",
        "clones",
    ],
    "Exploration": [
        "search",
        "search-semantic",
        "grep",
        "file",
        "symbol",
        "module",
        "trace",
        "deps",
        "uses",
        "fan",
        "impact",
        "relate",
        "endpoints",
        "metrics",
    ],
    "Reports & CI": [
        "report",
        "budget",
        "breaking",
        "coverage-gaps",
        "auth-gaps",
        "orphan-routes",
        "bus-factor",
        "simulate-departure",
        "suggest-reviewers",
        "dev-profile",
        "owner",
        "codeowners",
        "drift",
        "secrets",
        "supply-chain",
        "risk",
        "migration-safety",
        "api-drift",
        "path-coverage",
        "capsule",
        "rules",
        "vuln-map",
        "vuln-reach",
        "vulns",
        "sbom",
        "taint",
        "cga",
        "congestion",
    ],
    "Refactoring": [
        "dead",
        "orphan-imports",
        "flag-dead",
        "duplicates",
        "safe-delete",
        "split",
        "fn-coupling",
        "doc-staleness",
        "docs-coverage",
        "suggest-refactoring",
        "plan-refactor",
        "conventions",
        "sketch",
        "test-map",
        "test-pyramid",
        "why",
        "pr-risk",
        "invariants",
        "intent",
        "closure",
        "mutate",
        "test-scaffold",
    ],
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

    _GLOBAL_FLAGS = {
        "--json",
        "--compact",
        "--agent",
        "--sarif",
        "--include-excluded",
        "--detail",
    }
    _GLOBAL_VALUE_OPTIONS = {"--budget"}

    def parse_args(self, ctx, args):
        """Accept known global options before or after the subcommand.

        Click normally requires group options before the command
        (``roam --compact health``). Older docs and agent memories often use
        ``roam health --compact``; normalising that shape avoids a hard "No
        such option" while keeping command-specific parsing unchanged.
        """
        if args:
            args = self._normalise_global_option_position(list(args))
        return super().parse_args(ctx, args)

    def _normalise_global_option_position(self, args: list[str]) -> list[str]:
        if not args:
            return args

        cmd_index = None
        idx = 0
        while idx < len(args):
            token = args[idx]
            if token == "--":
                return args
            if token.startswith("-"):
                if token in self._GLOBAL_VALUE_OPTIONS and idx + 1 < len(args):
                    idx += 2
                    continue
                idx += 1
                continue
            cmd_index = idx
            break
        if cmd_index is None or cmd_index >= len(args) - 1:
            return args

        before = args[:cmd_index]
        command = args[cmd_index]
        after = args[cmd_index + 1 :]
        moved: list[str] = []
        kept: list[str] = []
        idx = 0
        while idx < len(after):
            token = after[idx]
            if token in self._GLOBAL_FLAGS:
                moved.append(token)
                idx += 1
                continue
            if token in self._GLOBAL_VALUE_OPTIONS and idx + 1 < len(after):
                moved.extend([token, after[idx + 1]])
                idx += 2
                continue
            if any(token.startswith(f"{opt}=") for opt in self._GLOBAL_VALUE_OPTIONS):
                moved.append(token)
                idx += 1
                continue
            kept.append(token)
            idx += 1

        if not moved:
            return args
        return before + moved + [command] + kept

    def list_commands(self, ctx):
        _ensure_plugin_commands_loaded()
        return sorted(_COMMANDS.keys())

    def get_command(self, ctx, cmd_name):
        # redacted — built-ins resolve without paying the
        # ~100ms entry-point-discovery cost. Only when the requested
        # command isn't in the static map do we fall back to plugin
        # discovery. Saves 100ms per CLI invocation for the 99% case
        # of users with no third-party roam plugins installed.
        if cmd_name in _COMMANDS:
            module_path, attr_name = _COMMANDS[cmd_name]
        else:
            _ensure_plugin_commands_loaded()
            if cmd_name not in _COMMANDS:
                return None
            module_path, attr_name = _COMMANDS[cmd_name]
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, attr_name)

    def resolve_command(self, ctx, args):
        """Resolve a subcommand, with a did-you-mean hint on typos.

        v12.14 — Click's default ``"No such command: 'contxt'"`` ends
        the conversation; we can do better. When the requested name
        isn't in ``_COMMANDS`` we look for the closest existing names
        by edit distance and surface them in the UsageError so the
        agent can retry with the right command in one turn.

        redactedalso surface a deprecation note on stderr when the
        invoked command is in ``_DEPRECATED_COMMANDS`` so users know
        about a planned rename / replacement.
        """
        # redactedpre-resolve deprecation hint.
        if args:
            cmd_name = args[0]
            replacement = _DEPRECATED_COMMANDS.get(cmd_name)
            if replacement:
                click.echo(
                    f"NOTE: `roam {cmd_name}` is deprecated — use `roam {replacement}` instead.",
                    err=True,
                )
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            msg = str(exc)
            if "No such command" not in msg:
                raise
            # Click's UsageError exposes the bad token as its first arg
            # in some versions; fall back to parsing the message.
            bad = args[0] if args else ""
            bad = bad.strip("'\"")
            if not bad:
                raise
            import difflib

            _ensure_plugin_commands_loaded()
            close = difflib.get_close_matches(bad, list(_COMMANDS.keys()), n=3, cutoff=0.6)
            # redactedwhen no edit-distance match lands but the user
            # typed a phrase, route them through the ``ask`` classifier
            # so a natural-language attempt ("trace login flow") still
            # gets a useful suggestion.
            recipe_hint = None
            if not close and len(bad) >= 6:
                try:
                    from roam.ask.classifier import classify

                    matches = classify(bad)
                    # ``classify`` returns ``[(Recipe, score), ...]``; pick the top
                    # entry only when its score is above a confidence floor so
                    # one-word typos don't get force-routed into a recipe.
                    if matches and matches[0][1] >= 0.5:
                        recipe = matches[0][0]
                        recipe_hint = f'`roam ask "{bad}"` (matches recipe: {recipe.name})'
                except Exception:
                    recipe_hint = None
            if close:
                suggestions = ", ".join(f"`roam {c}`" for c in close)
                raise click.UsageError(f"No such command: '{bad}'. Did you mean {suggestions}?") from exc
            if recipe_hint:
                raise click.UsageError(f"No such command: '{bad}'. Try {recipe_hint}.") from exc
            raise

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
        """Categorized help display instead of flat alphabetical list.

        Phase-1.5 / 12.13 — performance: previously this method called
        ``self.get_command()`` for each priority-category command,
        which triggered ``importlib.import_module()`` on every cmd_*.py
        in the priority list. That added ~3.5 seconds of Python
        imports just to render the help banner. We now extract the
        short-help via AST without importing — reading the source
        file's first docstring is fast (sub-100ms for the whole
        priority set) and produces the identical output. Falls back
        to a live import only when AST extraction can't find the
        docstring.
        """
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
                help_text = _short_help_via_ast(cmd_name)
                if help_text is None:
                    cmd = self.get_command(ctx, cmd_name)
                    help_text = cmd.get_short_help_str(limit=60) if cmd else ""
                formatter.write(f"    {cmd_name:20s} {help_text}\n")
                shown.add(cmd_name)
            formatter.write("\n")

        remaining = sorted(c for c in _COMMANDS if c not in shown)
        if remaining:
            formatter.write(f"  More Commands ({len(remaining)}):\n")
            formatter.write(f"    {', '.join(remaining)}\n\n")

        formatter.write("  Run `roam <command> --help` for details on any command.\n")
        # V6 — persist any newly-cached short-help entries.
        _save_short_help_cache_if_dirty()


# redacted — `_short_help_via_ast` is called 126x by `roam --help`,
# each call AST-parses the cmd_*.py file. ~640ms total. Disk cache keyed
# by source-file mtime collapses repeat invocations to a single dict lookup.
_SHORT_HELP_CACHE_PATH = os.path.expanduser("~/.roam-cli-cache/short-help.json")
_short_help_disk_cache: dict | None = None
_short_help_disk_cache_dirty = False


def _load_short_help_cache() -> dict:
    global _short_help_disk_cache
    if _short_help_disk_cache is not None:
        return _short_help_disk_cache
    try:
        import json as _json

        with open(_SHORT_HELP_CACHE_PATH, encoding="utf-8") as fh:
            _short_help_disk_cache = _json.load(fh)
    except (OSError, ValueError):
        _short_help_disk_cache = {}
    return _short_help_disk_cache


def _save_short_help_cache_if_dirty() -> None:
    global _short_help_disk_cache_dirty
    if not _short_help_disk_cache_dirty or _short_help_disk_cache is None:
        return
    try:
        import json as _json

        os.makedirs(os.path.dirname(_SHORT_HELP_CACHE_PATH), exist_ok=True)
        with open(_SHORT_HELP_CACHE_PATH, "w", encoding="utf-8") as fh:
            _json.dump(_short_help_disk_cache, fh)
        _short_help_disk_cache_dirty = False
    except OSError:
        pass


def _short_help_via_ast(cmd_name: str) -> str | None:
    """Extract a Click short-help string from cmd_*.py without importing.

    Click's ``get_short_help_str()`` reads the docstring of the function
    decorated with ``@click.command``, truncates at the first sentence,
    and limits to 60 chars. We reproduce that via Python's ``ast`` —
    no Click load, no cmd module import, no cascade of heavy deps.

    Returns ``None`` when the cmd file or expected attribute is absent;
    the caller falls back to the live ``self.get_command()`` path.
    """
    global _short_help_disk_cache_dirty
    target = _COMMANDS.get(cmd_name)
    if not target:
        return None
    module_path, attr_name = target
    # cli.py lives at src/roam/cli.py; cmd modules at src/roam/commands/cmd_*.py.
    # Build the path from the module dotted path relative to the package root,
    # not relative to cli.py.
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel = module_path.replace(".", os.sep) + ".py"
    src_path = os.path.join(pkg_root, rel)
    if not os.path.isfile(src_path):
        return None

    # V6 — cache check (key includes mtime so source edits invalidate).
    try:
        mtime = os.path.getmtime(src_path)
    except OSError:
        mtime = 0.0
    cache = _load_short_help_cache()
    cache_key = f"{module_path}:{attr_name}"
    cached = cache.get(cache_key)
    if cached and cached.get("mtime") == mtime:
        return cached.get("text") or None

    try:
        import ast as _ast

        with open(src_path, encoding="utf-8") as fh:
            tree = _ast.parse(fh.read(), filename=src_path)
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        # Find the function definition matching attr_name. Click commands
        # are functions decorated with @click.command (or named decorators).
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == attr_name:
            doc = _ast.get_docstring(node) or ""
            # Click's behaviour: take the first paragraph (up to blank line),
            # strip trailing punctuation, cap at 60 chars + "...".
            first_para = doc.split("\n\n", 1)[0].strip()
            first_line = " ".join(first_para.split())
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
            cache[cache_key] = {"mtime": mtime, "text": first_line}
            _short_help_disk_cache_dirty = True
            return first_line
    return None


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
        issues.append(f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.9")

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

    m = re.match(r"^(\w+)\s*(>=|<=|>|<|=)\s*(\d+(?:\.\d+)?)$", gate_expr.strip())
    if not m:
        return True  # can't parse, pass by default
    key, op, val_str = m.groups()
    val = float(val_str)

    actual = data.get(key)
    if actual is None:
        return True  # key not found, pass

    actual = float(actual)
    if op == ">=":
        return actual >= val
    if op == "<=":
        return actual <= val
    if op == ">":
        return actual > val
    if op == "<":
        return actual < val
    if op == "=":
        return actual == val
    return True


def _run_help_all(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback for --help-all: print every command + short help.

    The default ``roam --help`` shows priority categories + a flat
    "More Commands" name list (66 names, no descriptions). Agents
    mapping the territory often want every command's one-liner;
    --help-all renders that without categorisation, sub-second
    because the same AST short-help extraction --help uses.
    """
    if not value or ctx.resilient_parsing:
        return
    _ensure_plugin_commands_loaded()
    click.echo("Usage: roam [OPTIONS] COMMAND [ARGS]...\n")
    click.echo(f"All {len(_COMMANDS)} invokable command names:\n")
    for cmd_name in sorted(_COMMANDS):
        help_text = _short_help_via_ast(cmd_name) or ""
        click.echo(f"  {cmd_name:32s} {help_text}")
    click.echo()
    click.echo("Run `roam <command> --help` for details on any command.")
    ctx.exit(0)


@click.group(cls=LazyGroup)
@click.version_option(package_name="roam-code")
@click.option(
    "--check",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_run_check,
    help="Quick setup verification: checks Python, tree-sitter, git, SQLite",
)
@click.option(
    "--help-all",
    "help_all",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_run_help_all,
    help="Print every command (no categories, no truncation) and exit.",
)
@click.option("--json", "json_mode", is_flag=True, help="Output in JSON format")
@click.option("--compact", is_flag=True, help="Compact output: TSV tables, minimal JSON envelope")
@click.option("--agent", is_flag=True, help="Agent mode: compact JSON with 500-token default budget")
@click.option(
    "--sarif",
    "sarif_mode",
    is_flag=True,
    help="Output in SARIF 2.1.0 format (for dead, health, complexity, rules, secrets, algo)",
)
@click.option("--budget", type=int, default=0, help="Max output tokens (0=unlimited)")
@click.option(
    "--include-excluded",
    is_flag=True,
    help="Include files normally excluded by .roamignore / config / built-in patterns",
)
@click.option("--detail", is_flag=True, help="Show full detailed output instead of compact summary")
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
    ctx.obj["json"] = json_mode
    ctx.obj["compact"] = compact
    ctx.obj["agent"] = agent
    ctx.obj["sarif"] = sarif_mode
    ctx.obj["budget"] = budget
    ctx.obj["include_excluded"] = include_excluded
    ctx.obj["detail"] = detail

    # redactedopt-in local telemetry. Records (cmd, duration_ms,
    # exit_code) when ROAM_TELEMETRY_LOCAL=1. Strictly local; no
    # network. Recording itself is no-op when disabled, so the
    # uninstrumented hot path stays unaffected.
    import time as _time

    from roam.telemetry import record as _telemetry_record

    _start = _time.perf_counter()

    def _on_close():
        try:
            cmd_name = ctx.invoked_subcommand or "<root>"
            duration_ms = int((_time.perf_counter() - _start) * 1000)
            # exit code propagates through SystemExit; default 0 if not raised.
            _telemetry_record(cmd_name, duration_ms, exit_code=0)
        except Exception:
            pass

    ctx.call_on_close(_on_close)
