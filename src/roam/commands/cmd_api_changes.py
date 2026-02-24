"""Detect breaking and non-breaking API changes between git refs.

Extends the basic ``breaking`` command with full API surface comparison:
visibility changes, type changes, optional param additions, and severity levels.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import (
    abbrev_kind,
    format_signature,
    json_envelope,
    to_json,
)
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

SEVERITY_BREAKING = "breaking"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_BREAKING: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

# Change categories and their default severities
_CHANGE_CATEGORIES = {
    "REMOVED": SEVERITY_BREAKING,
    "SIGNATURE_CHANGED": SEVERITY_BREAKING,
    "VISIBILITY_REDUCED": SEVERITY_BREAKING,
    "RENAMED": SEVERITY_WARNING,
    "TYPE_CHANGED": SEVERITY_WARNING,
    "ADDED": SEVERITY_INFO,
    "PARAM_ADDED_OPTIONAL": SEVERITY_INFO,
    "DEPRECATED": SEVERITY_INFO,
}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_changed_files(root: Path, ref: str) -> list[str]:
    """Return files changed between *ref* and the working tree (indexed state)."""
    cmd = ["git", "diff", "--name-only", ref]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return []
        return [
            p.replace("\\", "/")
            for p in result.stdout.strip().splitlines()
            if p.strip()
        ]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _git_show(root: Path, ref: str, filepath: str) -> bytes | None:
    """Return the content of *filepath* at *ref*, or None if it didn't exist."""
    cmd = ["git", "show", f"{ref}:{filepath}"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _git_default_branch(root: Path) -> str:
    """Detect the default branch (main or master), fallback to HEAD~1."""
    for branch in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=str(root),
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return branch
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "HEAD~1"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_source_bytes(source: bytes, language: str):
    """Parse *source* bytes with tree-sitter for the given language."""
    from roam.index.parser import GRAMMAR_ALIASES

    grammar = GRAMMAR_ALIASES.get(language, language)

    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser(grammar)
    except Exception:
        return None, None, None

    try:
        tree = parser.parse(source)
    except Exception:
        return None, None, None

    return tree, source, language


def _extract_symbols_from_source(source: bytes, file_path: str) -> list[dict]:
    """Parse *source* bytes and extract symbols for *file_path*."""
    from roam.languages.registry import get_language_for_file, get_extractor_for_file
    from roam.index.symbols import extract_symbols

    language = get_language_for_file(file_path)
    if language is None:
        return []

    extractor = get_extractor_for_file(file_path)
    if extractor is None:
        return []

    tree, src, lang = _parse_source_bytes(source, language)
    if tree is None:
        return []

    return extract_symbols(tree, src, file_path, extractor)


# ---------------------------------------------------------------------------
# DB symbol lookup
# ---------------------------------------------------------------------------


def _get_current_symbols(conn, file_path: str) -> list[dict]:
    """Fetch current symbols for *file_path* from the index DB."""
    row = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT id FROM files WHERE path LIKE ? LIMIT 1",
            (f"%{file_path}",),
        ).fetchone()
    if not row:
        return []

    file_id = row["id"]
    rows = conn.execute(
        "SELECT name, qualified_name, kind, signature, line_start, line_end, "
        "visibility, is_exported FROM symbols WHERE file_id = ?",
        (file_id,),
    ).fetchall()

    return [
        {
            "name": r["name"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "signature": r["signature"],
            "line_start": r["line_start"],
            "line_end": r["line_end"],
            "visibility": r["visibility"],
            "is_exported": bool(r["is_exported"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def _key(sym: dict) -> str:
    """Unique key for matching: qualified_name or name."""
    return sym.get("qualified_name") or sym.get("name", "")


def _sig_normalise(sig: str | None, *, max_len: int = 200) -> str:
    """Normalise a signature for comparison."""
    if not sig:
        return ""
    normed = " ".join(sig.replace("\r", "").split())
    return normed[:max_len]


def _display_sig(sig: str | None, max_len: int = 60) -> str:
    """Extract a readable one-line display signature."""
    if not sig:
        return ""
    for line in sig.replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("func "):
            return format_signature(stripped, max_len)
    return format_signature(sig.split("\n")[0].strip(), max_len)


def _similarity(a: str, b: str) -> float:
    """Return 0..1 similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def _is_private_name(name: str) -> bool:
    """Check if a symbol name follows private naming conventions."""
    return name.startswith("_") and not name.startswith("__")


def _extract_params(sig: str | None) -> list[str]:
    """Extract parameter names from a function signature."""
    if not sig:
        return []
    # Try to find params between parentheses
    match = re.search(r'\(([^)]*)\)', sig)
    if not match:
        return []
    params_str = match.group(1)
    if not params_str.strip():
        return []
    params = []
    for param in params_str.split(","):
        param = param.strip()
        if not param or param == "self" or param == "cls":
            continue
        # Strip type annotations and defaults
        name = param.split(":")[0].split("=")[0].strip()
        if name.startswith("*"):
            name = name.lstrip("*")
        if name:
            params.append(name)
    return params


def _has_default(param_str: str) -> bool:
    """Check if a parameter string has a default value."""
    return "=" in param_str


def _extract_params_with_defaults(sig: str | None) -> list[tuple[str, bool]]:
    """Extract parameter (name, has_default) tuples from a function signature."""
    if not sig:
        return []
    match = re.search(r'\(([^)]*)\)', sig)
    if not match:
        return []
    params_str = match.group(1)
    if not params_str.strip():
        return []
    result = []
    for param in params_str.split(","):
        param = param.strip()
        if not param or param == "self" or param == "cls":
            continue
        name = param.split(":")[0].split("=")[0].strip()
        if name.startswith("*"):
            name = name.lstrip("*")
        has_def = _has_default(param)
        if name:
            result.append((name, has_def))
    return result


def _extract_return_type(sig: str | None) -> str:
    """Extract return type annotation from a signature."""
    if not sig:
        return ""
    # Look for -> annotation
    match = re.search(r'->\s*(.+?)(?:\s*:|$)', sig)
    if match:
        return match.group(1).strip().rstrip(":")
    return ""


def _is_deprecated(sym: dict) -> bool:
    """Check if a symbol has deprecation markers."""
    sig = sym.get("signature") or ""
    doc = sym.get("docstring") or ""
    combined = (sig + " " + doc).lower()
    return "deprecated" in combined or "@deprecated" in combined


def _exported_only(symbols: list[dict]) -> list[dict]:
    """Keep only exported / public symbols."""
    return [s for s in symbols if s.get("is_exported")]


# API-relevant kinds whose signatures represent a contract
_API_KINDS = {
    "function", "method", "class", "constructor",
    "interface", "trait", "struct",
}


def _compare_file_api(
    file_path: str,
    old_symbols: list[dict],
    new_symbols: list[dict],
) -> list[dict]:
    """Compare old vs new symbols for a single file and return all API changes.

    Each change dict has:
        category, severity, symbol_name, symbol_kind, file, line,
        old_signature, new_signature, description
    """
    old_exported = _exported_only(old_symbols)
    new_exported = _exported_only(new_symbols)

    old_by_key = {_key(s): s for s in old_exported}
    new_by_key = {_key(s): s for s in new_exported}

    # Also index ALL new symbols (including non-exported) for visibility checks
    all_new_by_key = {_key(s): s for s in new_symbols}

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    changes: list[dict] = []

    # 1. Symbols present in both: check for signature/type/visibility changes
    for k in old_keys & new_keys:
        old_sym = old_by_key[k]
        new_sym = new_by_key[k]

        # Signature changes (for API-relevant kinds)
        if old_sym["kind"] in _API_KINDS:
            old_sig = _sig_normalise(old_sym.get("signature"))
            new_sig = _sig_normalise(new_sym.get("signature"))

            if old_sig and new_sig and old_sig != new_sig:
                # Determine if it is a breaking signature change or just optional param
                old_params = _extract_params_with_defaults(old_sig)
                new_params = _extract_params_with_defaults(new_sig)

                old_names = [p[0] for p in old_params]
                new_names = [p[0] for p in new_params]

                # Check if only new optional params were added
                if (
                    len(new_params) > len(old_params)
                    and new_names[:len(old_names)] == old_names
                    and all(has_def for _, has_def in new_params[len(old_params):])
                ):
                    # Non-breaking: only added optional params
                    added_param_names = [p[0] for p in new_params[len(old_params):]]
                    changes.append(_make_change(
                        category="PARAM_ADDED_OPTIONAL",
                        sym=new_sym,
                        file_path=file_path,
                        old_sig=old_sym.get("signature"),
                        new_sig=new_sym.get("signature"),
                        description=f"Added optional param(s): {', '.join(added_param_names)}",
                    ))
                else:
                    # Check for return type changes
                    old_ret = _extract_return_type(old_sig)
                    new_ret = _extract_return_type(new_sig)
                    if old_ret and new_ret and old_ret != new_ret and old_names == new_names:
                        changes.append(_make_change(
                            category="TYPE_CHANGED",
                            sym=new_sym,
                            file_path=file_path,
                            old_sig=old_sym.get("signature"),
                            new_sig=new_sym.get("signature"),
                            description=f"Return type: {old_ret} -> {new_ret}",
                        ))
                    else:
                        # General signature change (breaking)
                        changes.append(_make_change(
                            category="SIGNATURE_CHANGED",
                            sym=new_sym,
                            file_path=file_path,
                            old_sig=old_sym.get("signature"),
                            new_sig=new_sym.get("signature"),
                            description="Signature changed",
                        ))

        # Deprecation detection
        if not _is_deprecated(old_sym) and _is_deprecated(new_sym):
            changes.append(_make_change(
                category="DEPRECATED",
                sym=new_sym,
                file_path=file_path,
                old_sig=old_sym.get("signature"),
                new_sig=new_sym.get("signature"),
                description="Symbol marked as deprecated",
            ))

    # 2. Missing keys: symbol was in old but not in new exported set
    missing_keys = old_keys - new_keys
    added_keys = new_keys - old_keys

    # Try fuzzy rename matching first
    added_map = {k: new_by_key[k] for k in added_keys}
    rename_used_added: set[str] = set()

    for mk in sorted(missing_keys):
        old_sym = old_by_key[mk]

        # Check if it became private (visibility reduced)
        new_all = all_new_by_key.get(mk)
        if new_all is not None and not new_all.get("is_exported"):
            changes.append(_make_change(
                category="VISIBILITY_REDUCED",
                sym=old_sym,
                file_path=file_path,
                old_sig=old_sym.get("signature"),
                new_sig=new_all.get("signature"),
                description=f"Was {old_sym.get('visibility', 'public')}, now {new_all.get('visibility', 'private')}",
            ))
            continue

        # Also detect visibility by naming convention change
        # (symbol with same name but _-prefixed in new)
        private_name = "_" + old_sym["name"]
        private_match = None
        for nk, ns in all_new_by_key.items():
            if ns["name"] == private_name and ns["kind"] == old_sym["kind"]:
                private_match = ns
                break
        if private_match is not None:
            changes.append(_make_change(
                category="VISIBILITY_REDUCED",
                sym=old_sym,
                file_path=file_path,
                old_sig=old_sym.get("signature"),
                new_sig=private_match.get("signature"),
                description=f"Renamed to _{old_sym['name']} (now private)",
            ))
            rename_used_added.add(_key(private_match))
            continue

        # Try rename matching among added symbols
        best_match = None
        best_score = 0.0

        for ak, new_sym in added_map.items():
            if ak in rename_used_added:
                continue
            if old_sym["kind"] != new_sym["kind"]:
                continue

            name_sim = _similarity(old_sym["name"], new_sym["name"])
            old_line = old_sym.get("line_start") or 0
            new_line = new_sym.get("line_start") or 0
            line_dist = abs(old_line - new_line)
            line_bonus = max(0, (10 - line_dist) / 10) * 0.3

            old_sig = _sig_normalise(old_sym.get("signature"))
            new_sig = _sig_normalise(new_sym.get("signature"))
            sig_sim = _similarity(old_sig, new_sig) * 0.2 if old_sig and new_sig else 0

            score = name_sim + line_bonus + sig_sim

            if score > best_score:
                best_score = score
                best_match = ak

        if best_match is not None and best_score >= 0.6:
            new_sym = added_map[best_match]
            rename_used_added.add(best_match)
            changes.append(_make_change(
                category="RENAMED",
                sym=old_sym,
                file_path=file_path,
                old_sig=old_sym.get("signature"),
                new_sig=new_sym.get("signature"),
                description=f"{old_sym['name']} -> {new_sym['name']}",
            ))
        else:
            # Truly removed
            changes.append(_make_change(
                category="REMOVED",
                sym=old_sym,
                file_path=file_path,
                old_sig=old_sym.get("signature"),
                new_sig=None,
                description=f"Public {old_sym['kind']} removed",
            ))

    # 3. Added symbols (new exports not matched to renames)
    for ak in sorted(added_keys):
        if ak in rename_used_added:
            continue
        new_sym = new_by_key[ak]
        changes.append(_make_change(
            category="ADDED",
            sym=new_sym,
            file_path=file_path,
            old_sig=None,
            new_sig=new_sym.get("signature"),
            description=f"New public {new_sym['kind']}",
        ))

    return changes


def _make_change(
    *,
    category: str,
    sym: dict,
    file_path: str,
    old_sig: str | None,
    new_sig: str | None,
    description: str,
) -> dict:
    """Construct a normalised change dict."""
    return {
        "category": category,
        "severity": _CHANGE_CATEGORIES[category],
        "symbol_name": sym["name"],
        "symbol_kind": sym["kind"],
        "file": file_path,
        "line": sym.get("line_start"),
        "old_signature": old_sig or "",
        "new_signature": new_sig or "",
        "description": description,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_change_text(change: dict) -> str:
    """Format a single change dict for text output."""
    kind = abbrev_kind(change["symbol_kind"])
    loc = f"{change['file']}:{change['line']}" if change.get("line") else change["file"]

    cat = change["category"]

    if cat == "REMOVED":
        sig = _display_sig(change["old_signature"]) or change["symbol_name"]
        return f"  REMOVED {kind} {sig}    {loc}"
    elif cat == "SIGNATURE_CHANGED":
        old = _display_sig(change["old_signature"]) or change["symbol_name"]
        new = _display_sig(change["new_signature"]) or change["symbol_name"]
        lines = [f"  SIGNATURE {kind} {change['symbol_name']}    {loc}"]
        lines.append(f"    was: {old}")
        lines.append(f"    now: {new}")
        return "\n".join(lines)
    elif cat == "VISIBILITY_REDUCED":
        return f"  VISIBILITY {kind} {change['symbol_name']}    {loc}\n    {change['description']}"
    elif cat == "RENAMED":
        return f"  RENAMED {kind} {change['description']}    {loc}"
    elif cat == "TYPE_CHANGED":
        return f"  TYPE {kind} {change['symbol_name']}    {loc}\n    {change['description']}"
    elif cat == "ADDED":
        sig = _display_sig(change["new_signature"]) or change["symbol_name"]
        return f"  ADDED {kind} {sig}    {loc}"
    elif cat == "PARAM_ADDED_OPTIONAL":
        return f"  OPTIONAL_PARAM {kind} {change['symbol_name']}    {loc}\n    {change['description']}"
    elif cat == "DEPRECATED":
        return f"  DEPRECATED {kind} {change['symbol_name']}    {loc}"
    else:
        return f"  {cat} {kind} {change['symbol_name']}    {loc}"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("api-changes")
@click.option(
    "--base",
    default=None,
    help="Git ref to compare against (default: HEAD~1 or main).",
)
@click.option(
    "--severity",
    type=click.Choice(["breaking", "warning", "info"], case_sensitive=False),
    default="warning",
    help="Minimum severity to report (default: warning).",
)
@click.option(
    "--changed",
    "changed_only",
    is_flag=True,
    help="Only analyze files changed in git diff.",
)
@click.pass_context
def api_changes(ctx, base, severity, changed_only):
    """Detect breaking and non-breaking API changes vs a git ref.

    Compares the current exported API surface against BASE (default: HEAD~1)
    and reports removed symbols, changed signatures, visibility reductions,
    type changes, renames, added symbols, and optional param additions.

    Severity levels: breaking > warning > info.
    Use --severity=info to see all changes including additions.
    Use --severity=breaking to see only breaking changes.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    ensure_index()
    root = find_project_root()

    # Resolve base ref
    if base is None:
        base = _git_default_branch(root)

    min_severity = _SEVERITY_ORDER.get(severity.lower(), 1)

    # 1. Find changed files
    changed = _git_changed_files(root, base)
    if not changed:
        verdict = f"No changed files vs {base}."
        if json_mode:
            click.echo(to_json(json_envelope(
                "api-changes",
                summary={
                    "verdict": verdict,
                    "breaking_count": 0,
                    "warning_count": 0,
                    "info_count": 0,
                    "base_ref": base,
                },
                base_ref=base,
                changes=[],
            )))
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # 2. Collect all changes across files
    all_changes: list[dict] = []

    with open_db(readonly=True) as conn:
        for fpath in changed:
            # Get old file content from the ref
            old_source = _git_show(root, base, fpath)
            if old_source is None:
                # File is new — extract new symbols as ADDED
                new_symbols = _get_current_symbols(conn, fpath)
                for sym in _exported_only(new_symbols):
                    all_changes.append(_make_change(
                        category="ADDED",
                        sym=sym,
                        file_path=fpath,
                        old_sig=None,
                        new_sig=sym.get("signature"),
                        description=f"New public {sym['kind']} (new file)",
                    ))
                continue

            old_symbols = _extract_symbols_from_source(old_source, fpath)
            if not old_symbols:
                continue

            # Get current symbols: prefer DB, fall back to file parse
            new_symbols = _get_current_symbols(conn, fpath)
            if not new_symbols:
                # File might have been deleted
                current_path = root / fpath
                if current_path.exists():
                    current_source = current_path.read_bytes()
                    new_symbols = _extract_symbols_from_source(current_source, fpath)

            file_changes = _compare_file_api(fpath, old_symbols, new_symbols)
            all_changes.extend(file_changes)

    # 3. Filter by severity
    filtered = [
        c for c in all_changes
        if _SEVERITY_ORDER[c["severity"]] <= min_severity
    ]

    # Sort: breaking first, then warning, then info; within each, by file+line
    filtered.sort(key=lambda c: (
        _SEVERITY_ORDER[c["severity"]],
        c["file"],
        c.get("line") or 0,
    ))

    # Count by severity (from all changes, not filtered)
    breaking_count = sum(1 for c in all_changes if c["severity"] == SEVERITY_BREAKING)
    warning_count = sum(1 for c in all_changes if c["severity"] == SEVERITY_WARNING)
    info_count = sum(1 for c in all_changes if c["severity"] == SEVERITY_INFO)

    # Collect affected files
    affected_files = sorted(set(c["file"] for c in all_changes))

    # Build verdict
    parts = []
    if breaking_count:
        parts.append(f"{breaking_count} BREAKING")
    if warning_count:
        parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
    if info_count:
        parts.append(f"{info_count} info")
    if parts:
        verdict = f"{', '.join(parts)} in {len(affected_files)} file{'s' if len(affected_files) != 1 else ''}"
    else:
        verdict = f"No API changes vs {base}."

    if json_mode:
        click.echo(to_json(json_envelope(
            "api-changes",
            summary={
                "verdict": verdict,
                "breaking_count": breaking_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "base_ref": base,
            },
            budget=token_budget,
            base_ref=base,
            changes=filtered,
        )))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")

    if not filtered:
        return

    click.echo()

    # Group by severity
    by_severity: dict[str, list[dict]] = defaultdict(list)
    for c in filtered:
        by_severity[c["severity"]].append(c)

    if SEVERITY_BREAKING in by_severity:
        click.echo("BREAKING:")
        for c in by_severity[SEVERITY_BREAKING]:
            click.echo(_format_change_text(c))
        click.echo()

    if SEVERITY_WARNING in by_severity:
        click.echo("WARNING:")
        for c in by_severity[SEVERITY_WARNING]:
            click.echo(_format_change_text(c))
        click.echo()

    if SEVERITY_INFO in by_severity:
        click.echo("INFO:")
        for c in by_severity[SEVERITY_INFO]:
            click.echo(_format_change_text(c))
        click.echo()
