"""Detect potentially stale feature flag code (conditionally-dead code behind flags)."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# Feature flag API call patterns — compiled once at module level
# ---------------------------------------------------------------------------

_FLAG_PATTERN_DEFS: list[dict] = [
    # --- LaunchDarkly ---
    {
        "provider": "LaunchDarkly",
        "pattern": r"""(?:\.variation|\.bool_variation|\.string_variation|\.json_variation|\.int_variation|\.float_variation|\.double_variation)\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    {
        "provider": "LaunchDarkly",
        "pattern": r"""ldclient\.get\(\s*\)\s*\.(?:variation|bool_variation|string_variation|json_variation|int_variation|float_variation|double_variation)\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    # --- Unleash ---
    {
        "provider": "Unleash",
        "pattern": r"""\.(?:is_enabled|isEnabled)\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    {
        "provider": "Unleash",
        "pattern": r"""\.get_variant\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    # --- Split ---
    {
        "provider": "Split",
        "pattern": r"""\.(?:get_treatment|getTreatment)\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    # --- Generic flag functions ---
    {
        "provider": "generic",
        "pattern": r"""(?:feature_flag|is_feature_enabled|isFeatureEnabled|has_feature|check_feature|toggle|is_on|isOn)\s*\(\s*['"]([\w.:-]+)['"]""",
    },
    {
        "provider": "generic",
        "pattern": r"""feature_enabled\?\s*\(\s*[':]([\w.:-]+)['"]?\)""",
    },
    # --- Environment variable flags ---
    {
        "provider": "env-var",
        "pattern": r"""os\.environ\.get\s*\(\s*['"]FEATURE_([\w]+)['"]""",
    },
    {
        "provider": "env-var",
        "pattern": r"""os\.environ\s*\[\s*['"]FEATURE_([\w]+)['"]""",
    },
    {
        "provider": "env-var",
        "pattern": r"""process\.env\.FEATURE_([\w]+)""",
    },
    {
        "provider": "env-var",
        "pattern": r"""ENV\s*\[\s*['"]FEATURE_([\w]+)['"]""",
    },
]

# Compile all regexes once
_COMPILED_PATTERNS: list[dict] = []
for _pdef in _FLAG_PATTERN_DEFS:
    _COMPILED_PATTERNS.append(
        {
            "provider": _pdef["provider"],
            "regex": re.compile(_pdef["pattern"]),
        }
    )

# Extensions worth scanning for flag usage (source code, not images/binaries)
_SCANNABLE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".go",
        ".rb",
        ".rs",
        ".cs",
        ".php",
        ".scala",
        ".swift",
        ".m",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".vue",
        ".svelte",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".lua",
        ".r",
        ".R",
        ".dart",
        ".groovy",
        ".clj",
        ".cls",
        ".trigger",
    }
)

# Directories to always skip during scanning
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "venv",
        ".venv",
        "env",
        ".env",
        "dist",
        "build",
        ".eggs",
        ".next",
        ".nuxt",
        ".roam",
    }
)

# Pattern to extract default/fallback values from flag calls
# Matches second argument after the flag name: variation("flag", default_value)
_DEFAULT_VALUE_RE = re.compile(
    r"""['"]\s*,\s*(?:"""
    r"""['"](\w+)['"]"""  # string default
    r"""|"""
    r"""(true|false|True|False|nil|null|None)"""  # boolean/null default
    r"""|"""
    r"""(\d+)"""  # numeric default
    r""")"""
)


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------


def _in_skip_dir(rel_path: str) -> bool:
    """Check if a relative path is under a directory that should be skipped."""
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in _SKIP_DIRS for p in parts)


def _is_scannable(rel_path: str) -> bool:
    """Check if a file extension is worth scanning for flags."""
    _, ext = os.path.splitext(rel_path)
    return ext.lower() in _SCANNABLE_EXTENSIONS


def scan_file_for_flags(file_path: str) -> list[dict]:
    """Scan a single file for feature flag API calls.

    Returns a list of dicts with keys:
        file, line, provider, flag_name, default_value, raw_line
    """
    findings: list[dict] = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, start=1):
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                for pat in _COMPILED_PATTERNS:
                    match = pat["regex"].search(line)
                    if match:
                        flag_name = match.group(1)
                        # For env-var provider, prefix with FEATURE_ for clarity
                        if pat["provider"] == "env-var":
                            flag_name = "FEATURE_" + flag_name

                        # Try to extract default value from the remainder of the line
                        remainder = line[match.end() :]
                        default_value = None
                        default_match = _DEFAULT_VALUE_RE.search(remainder)
                        if default_match:
                            default_value = (
                                default_match.group(1)
                                or default_match.group(2)
                                or default_match.group(3)
                            )

                        findings.append(
                            {
                                "file": file_path,
                                "line": line_num,
                                "provider": pat["provider"],
                                "flag_name": flag_name,
                                "default_value": default_value,
                                "raw_line": stripped[:200],
                            }
                        )
                        break  # one match per line is enough
    except (OSError, UnicodeDecodeError):
        pass

    return findings


def scan_project_for_flags(
    project_root: Path,
    use_index: bool = True,
    include_tests: bool = False,
) -> list[dict]:
    """Scan all indexed files in a project for feature flag calls.

    If use_index is True, reads file paths from the roam index DB.
    Otherwise falls back to walking the filesystem.

    Returns a list of finding dicts sorted by flag name then file path.
    """
    root = Path(project_root).resolve()

    if use_index:
        try:
            with open_db(readonly=True) as conn:
                rows = conn.execute("SELECT path FROM files").fetchall()
                file_paths = [row["path"] for row in rows]
        except Exception:
            file_paths = _walk_for_files(root)
    else:
        file_paths = _walk_for_files(root)

    # Test/fixture path detection
    test_segments = frozenset(
        {"tests", "test", "__tests__", "spec", "fixtures", "docs", "examples"}
    )
    test_file_re = re.compile(r"^test_.*|.*_test\.[^.]+$", re.IGNORECASE)

    all_findings: list[dict] = []
    for rel_path in file_paths:
        if not _is_scannable(rel_path):
            continue
        if _in_skip_dir(rel_path):
            continue

        # Suppress test/fixture/docs files unless --include-tests
        if not include_tests:
            normed = rel_path.replace("\\", "/")
            parts = normed.split("/")
            basename = parts[-1] if parts else ""
            is_test = False
            for part in parts[:-1]:
                if part in test_segments:
                    is_test = True
                    break
            if not is_test and test_file_re.match(basename):
                is_test = True
            if is_test:
                continue

        full_path = root / rel_path
        if not full_path.is_file():
            continue

        file_findings = scan_file_for_flags(str(full_path))
        # Store relative path in findings for cleaner output
        for f in file_findings:
            f["file"] = rel_path
        all_findings.extend(file_findings)

    all_findings.sort(key=lambda f: (f["flag_name"], f["file"], f["line"]))
    return all_findings


def _walk_for_files(root: Path) -> list[str]:
    """Walk the filesystem to find scannable files (fallback)."""
    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                rel = os.path.relpath(full, root).replace("\\", "/")
            except (ValueError, OSError):
                continue
            result.append(rel)
    return result


def _load_known_stale(config_path: str) -> set[str]:
    """Load a list of known-stale flag names from a config file (one per line)."""
    stale: set[str] = set()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    stale.add(line)
    except (OSError, UnicodeDecodeError):
        pass
    return stale


def analyze_flags(
    findings: list[dict],
    known_stale: set[str] | None = None,
) -> list[dict]:
    """Analyze flag findings for staleness indicators.

    Returns a list of flag summary dicts with keys:
        flag_name, provider, locations, count, staleness, reasons,
        default_value, is_known_stale
    """
    if known_stale is None:
        known_stale = set()

    # Group findings by flag name
    by_flag: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_flag[f["flag_name"]].append(f)

    results: list[dict] = []
    for flag_name, flag_findings in sorted(by_flag.items()):
        count = len(flag_findings)
        providers = sorted({f["provider"] for f in flag_findings})
        locations = [
            {"file": f["file"], "line": f["line"]} for f in flag_findings
        ]

        # Collect all default values seen for this flag
        defaults = [
            f["default_value"]
            for f in flag_findings
            if f["default_value"] is not None
        ]
        unique_defaults = sorted(set(defaults)) if defaults else []

        # --- Staleness heuristics ---
        reasons: list[str] = []
        staleness = "ok"

        # Known-stale from config file
        if flag_name in known_stale:
            reasons.append("listed in known-stale config")
            staleness = "stale"

        # Single-location flag: likely leftover
        if count == 1:
            reasons.append("only referenced in 1 location")
            if staleness != "stale":
                staleness = "likely-stale"

        # Always same default value and that default is a boolean
        if unique_defaults and len(unique_defaults) == 1:
            val = unique_defaults[0].lower() if unique_defaults[0] else ""
            if val in ("false", "true", "0", "1", "nil", "null", "none"):
                reasons.append(
                    f"always checked with same default ({unique_defaults[0]})"
                )
                if staleness == "ok":
                    staleness = "suspect"

        # All references in a single file
        unique_files = {f["file"] for f in flag_findings}
        if count > 1 and len(unique_files) == 1:
            reasons.append("all references in single file")
            if staleness == "ok":
                staleness = "suspect"

        results.append(
            {
                "flag_name": flag_name,
                "provider": ", ".join(providers),
                "locations": locations,
                "count": count,
                "staleness": staleness,
                "reasons": reasons,
                "default_values": unique_defaults,
                "is_known_stale": flag_name in known_stale,
            }
        )

    # Sort: stale first, then likely-stale, then suspect, then ok
    staleness_order = {"stale": 0, "likely-stale": 1, "suspect": 2, "ok": 3}
    results.sort(key=lambda r: (staleness_order.get(r["staleness"], 9), r["flag_name"]))

    return results


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("flag-dead")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="File listing known-stale flag names (one per line)",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Include test files, fixtures, docs, and examples in scan",
)
@click.pass_context
def flag_dead(ctx, config_path, include_tests):
    """Detect potentially stale feature flag code (conditionally-dead code).

    Scans source files for feature flag API calls from LaunchDarkly,
    Unleash, Split, and generic patterns.  Identifies flags that may be
    stale based on usage patterns: single-location references, constant
    defaults, and flags listed in a known-stale config file.

    Unlike ``dead`` (which detects structurally unreferenced symbols via
    the call graph), this command detects code that is alive in the graph
    but gated behind feature flags that may never fire.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()

    project_root = find_project_root()

    # Load known-stale flags from config file if provided
    known_stale: set[str] = set()
    if config_path:
        known_stale = _load_known_stale(config_path)

    # Scan for flag usage
    findings = scan_project_for_flags(
        project_root, include_tests=include_tests
    )

    # Analyze flags for staleness
    flag_summaries = analyze_flags(findings, known_stale=known_stale)

    # Compute summary stats
    total_flags = len(flag_summaries)
    total_references = sum(f["count"] for f in flag_summaries)
    stale_count = sum(1 for f in flag_summaries if f["staleness"] == "stale")
    likely_stale_count = sum(
        1 for f in flag_summaries if f["staleness"] == "likely-stale"
    )
    suspect_count = sum(
        1 for f in flag_summaries if f["staleness"] == "suspect"
    )
    ok_count = sum(1 for f in flag_summaries if f["staleness"] == "ok")
    files_affected = len(
        {loc["file"] for f in flag_summaries for loc in f["locations"]}
    )

    if total_flags == 0:
        verdict = "No feature flags detected"
    else:
        parts = []
        if stale_count:
            parts.append(f"{stale_count} stale")
        if likely_stale_count:
            parts.append(f"{likely_stale_count} likely-stale")
        if suspect_count:
            parts.append(f"{suspect_count} suspect")
        if ok_count:
            parts.append(f"{ok_count} ok")
        status_str = ", ".join(parts)
        verdict = (
            f"{total_flags} flags found across {files_affected} files "
            f"({status_str})"
        )

    # --- JSON output ---
    if json_mode:
        envelope = json_envelope(
            "flag-dead",
            summary={
                "verdict": verdict,
                "total_flags": total_flags,
                "total_references": total_references,
                "files_affected": files_affected,
                "stale": stale_count,
                "likely_stale": likely_stale_count,
                "suspect": suspect_count,
                "ok": ok_count,
            },
            budget=token_budget,
            flags=[
                {
                    "flag_name": f["flag_name"],
                    "provider": f["provider"],
                    "count": f["count"],
                    "staleness": f["staleness"],
                    "reasons": f["reasons"],
                    "default_values": f["default_values"],
                    "is_known_stale": f["is_known_stale"],
                    "locations": f["locations"],
                }
                for f in flag_summaries
            ],
        )
        click.echo(to_json(envelope))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if not flag_summaries:
        click.echo("  No feature flag API calls detected in the codebase.")
        click.echo()
        click.echo("  Supported providers: LaunchDarkly, Unleash, Split, generic, env-var")
        return

    # Summary table
    rows = []
    for f in flag_summaries:
        staleness_label = f["staleness"].upper()
        defaults_str = (
            ", ".join(f["default_values"]) if f["default_values"] else "-"
        )
        reasons_str = "; ".join(f["reasons"]) if f["reasons"] else "-"
        rows.append(
            [
                f["flag_name"],
                f["provider"],
                str(f["count"]),
                staleness_label,
                defaults_str,
                reasons_str,
            ]
        )

    click.echo(
        format_table(
            ["Flag", "Provider", "Refs", "Status", "Defaults", "Reasons"],
            rows,
        )
    )
    click.echo()

    # Per-flag location details for stale/likely-stale/suspect flags
    flagged = [
        f for f in flag_summaries if f["staleness"] != "ok"
    ]
    if flagged:
        click.echo(f"  {len(flagged)} flags with staleness indicators:")
        click.echo()
        for f in flagged:
            click.echo(f"  {f['flag_name']} ({f['staleness'].upper()}):")
            for loc in f["locations"]:
                click.echo(f"    {loc['file']}:{loc['line']}")
            if f["reasons"]:
                click.echo(f"    Reasons: {'; '.join(f['reasons'])}")
            click.echo()

    # Totals
    click.echo(
        f"  {total_flags} flags, {total_references} references, "
        f"{files_affected} files"
    )
    if stale_count or likely_stale_count or suspect_count:
        click.echo()
        click.echo("  Recommendations:")
        if stale_count:
            click.echo(
                f"  - {stale_count} known-stale flags should be removed"
            )
        if likely_stale_count:
            click.echo(
                f"  - {likely_stale_count} likely-stale flags (single reference) "
                "should be reviewed for removal"
            )
        if suspect_count:
            click.echo(
                f"  - {suspect_count} suspect flags have constant defaults "
                "or are concentrated in single files"
            )
        click.echo(
            "  - Verify flag status in your feature flag dashboard before removing"
        )
