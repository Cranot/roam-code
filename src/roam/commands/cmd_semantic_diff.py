"""Show structural change summary: symbols added/removed/modified, changed imports."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from roam.db.connection import find_project_root
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


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


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_source_bytes(source: bytes, language: str):
    """Parse *source* bytes with tree-sitter for the given language.

    Returns (tree, source_bytes, effective_language) or (None, None, None).
    """
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
    """Parse *source* bytes and extract symbols for *file_path*.

    Returns a list of normalised symbol dicts.
    """
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


def _extract_references_from_source(source: bytes, file_path: str) -> list[dict]:
    """Parse *source* bytes and extract references (imports, calls) for *file_path*.

    Returns a list of normalised reference dicts.
    """
    from roam.languages.registry import get_language_for_file, get_extractor_for_file
    from roam.index.symbols import extract_references

    language = get_language_for_file(file_path)
    if language is None:
        return []

    extractor = get_extractor_for_file(file_path)
    if extractor is None:
        return []

    tree, src, lang = _parse_source_bytes(source, language)
    if tree is None:
        return []

    return extract_references(tree, src, file_path, extractor)


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def _sym_key(sym: dict) -> str:
    """Unique key for matching: qualified_name or name."""
    return sym.get("qualified_name") or sym.get("name", "")


def _count_lines(sym: dict) -> int | None:
    """Compute body line count from line_start/line_end."""
    start = sym.get("line_start")
    end = sym.get("line_end")
    if start is not None and end is not None:
        return max(1, end - start + 1)
    return None


def _extract_params(sig: str | None) -> list[str]:
    """Extract parameter names from a function signature string.

    Attempts to parse the parenthesized parameter list from the signature.
    """
    if not sig:
        return []
    # Find the parameter list between first ( and last )
    start = sig.find("(")
    end = sig.rfind(")")
    if start < 0 or end <= start:
        return []
    params_str = sig[start + 1:end].strip()
    if not params_str:
        return []
    # Split by comma and extract parameter names (skip type annotations)
    params = []
    for part in params_str.split(","):
        part = part.strip()
        if not part:
            continue
        # For "name: type = default", take just the name
        # For "type name", take the last token
        # Strip leading * and ** (Python), & (PHP), ... (JS rest)
        part = part.lstrip("*&.")
        # Remove default values
        if "=" in part:
            part = part[:part.index("=")].strip()
        # Remove type annotations (e.g. "name: int" -> "name")
        if ":" in part:
            part = part[:part.index(":")].strip()
        # Take the last word as the name (handles "int name" style)
        tokens = part.split()
        if tokens:
            name = tokens[-1].strip("*&")
            if name and name not in ("self", "cls", "this"):
                params.append(name)
    return params


def _compare_symbols(
    file_path: str,
    old_symbols: list[dict],
    new_symbols: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Compare old vs new symbols for a single file.

    Returns (added, removed, modified) where each entry is a dict
    carrying the relevant information for display.
    """
    old_by_key = {_sym_key(s): s for s in old_symbols if _sym_key(s)}
    new_by_key = {_sym_key(s): s for s in new_symbols if _sym_key(s)}

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    # Added symbols: in new but not old
    added = []
    for k in sorted(new_keys - old_keys):
        sym = new_by_key[k]
        lines = _count_lines(sym)
        added.append({
            "name": sym["name"],
            "kind": sym.get("kind", "unknown"),
            "file": file_path,
            "line": sym.get("line_start"),
            "lines": lines,
        })

    # Removed symbols: in old but not new
    removed = []
    for k in sorted(old_keys - new_keys):
        sym = old_by_key[k]
        removed.append({
            "name": sym["name"],
            "kind": sym.get("kind", "unknown"),
            "file": file_path,
            "line": sym.get("line_start"),
        })

    # Modified symbols: same key, but signature or body changed
    modified = []
    _SIG_KINDS = {
        "function", "method", "class", "constructor",
        "interface", "trait", "struct",
    }
    for k in sorted(old_keys & new_keys):
        old_sym = old_by_key[k]
        new_sym = new_by_key[k]
        changes = {}

        # Check signature change
        old_sig = (old_sym.get("signature") or "").strip()
        new_sig = (new_sym.get("signature") or "").strip()
        if old_sig and new_sig and old_sig != new_sig and old_sym.get("kind") in _SIG_KINDS:
            old_params = _extract_params(old_sig)
            new_params = _extract_params(new_sig)
            added_params = [p for p in new_params if p not in old_params]
            removed_params = [p for p in old_params if p not in new_params]
            changes["params"] = {
                "old_count": len(old_params),
                "new_count": len(new_params),
                "added": added_params,
                "removed": removed_params,
            }

        # Check body line count change
        old_lines = _count_lines(old_sym)
        new_lines = _count_lines(new_sym)
        if old_lines is not None and new_lines is not None and old_lines != new_lines:
            changes["body_lines"] = {
                "old": old_lines,
                "new": new_lines,
            }

        if changes:
            modified.append({
                "name": new_sym["name"],
                "kind": new_sym.get("kind", "unknown"),
                "file": file_path,
                "line": new_sym.get("line_start"),
                "changes": changes,
            })

    return added, removed, modified


def _compare_imports(
    file_path: str,
    old_refs: list[dict],
    new_refs: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Compare old vs new import references for a single file.

    Returns (imports_added, imports_removed) where each is a list of dicts.
    """
    def import_set(refs: list[dict]) -> set[str]:
        result = set()
        for r in refs:
            if r.get("kind") in ("import", "import_from"):
                target = r.get("target_name", "")
                imp_path = r.get("import_path", "")
                if target:
                    key = f"{imp_path}:{target}" if imp_path else target
                    result.add(key)
        return result

    old_imports = import_set(old_refs)
    new_imports = import_set(new_refs)

    added = []
    for imp in sorted(new_imports - old_imports):
        added.append({
            "file": file_path,
            "import": imp,
        })

    removed = []
    for imp in sorted(old_imports - new_imports):
        removed.append({
            "file": file_path,
            "import": imp,
        })

    return added, removed


# ---------------------------------------------------------------------------
# Read current file content from disk
# ---------------------------------------------------------------------------


def _read_current_file(root: Path, file_path: str) -> bytes | None:
    """Read the current version of a file from the working tree."""
    full_path = root / file_path
    try:
        return full_path.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("semantic-diff")
@click.option("--base", "base_ref", default="HEAD~1",
              help="Git ref to compare against (default: HEAD~1)")
@click.pass_context
def semantic_diff(ctx, base_ref):
    """Show structural change summary vs a git ref.

    Unlike textual git diff, this shows the semantic/structural meaning of
    changes: symbols added/removed/modified, signature changes, body size
    changes, and import edge changes.

    Compares the current working tree against BASE (default: HEAD~1).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # 1. Get changed files
    changed = _git_changed_files(root, base_ref)
    if not changed:
        if json_mode:
            click.echo(to_json(json_envelope(
                "semantic-diff",
                summary={
                    "verdict": "No changed files",
                    "files_changed": 0,
                    "symbols_added": 0,
                    "symbols_removed": 0,
                    "symbols_modified": 0,
                    "imports_added": 0,
                    "imports_removed": 0,
                },
                base_ref=base_ref,
                symbols_added=[],
                symbols_removed=[],
                symbols_modified=[],
                imports_added=[],
                imports_removed=[],
            )))
        else:
            click.echo(f"No changed files vs {base_ref}.")
        return

    # 2. For each changed file, parse old and new versions and compare
    all_added: list[dict] = []
    all_removed: list[dict] = []
    all_modified: list[dict] = []
    all_imports_added: list[dict] = []
    all_imports_removed: list[dict] = []
    files_analyzed = 0

    for fpath in changed:
        # Get old version from the ref
        old_source = _git_show(root, base_ref, fpath)

        # Get current version from disk
        new_source = _read_current_file(root, fpath)

        # Extract symbols from both versions
        old_symbols = _extract_symbols_from_source(old_source, fpath) if old_source else []
        new_symbols = _extract_symbols_from_source(new_source, fpath) if new_source else []

        # Extract references from both versions
        old_refs = _extract_references_from_source(old_source, fpath) if old_source else []
        new_refs = _extract_references_from_source(new_source, fpath) if new_source else []

        if not old_symbols and not new_symbols:
            continue

        files_analyzed += 1

        # Compare symbols
        added, removed, modified = _compare_symbols(fpath, old_symbols, new_symbols)
        all_added.extend(added)
        all_removed.extend(removed)
        all_modified.extend(modified)

        # Compare imports
        imp_added, imp_removed = _compare_imports(fpath, old_refs, new_refs)
        all_imports_added.extend(imp_added)
        all_imports_removed.extend(imp_removed)

    # Sort for stable output
    all_added.sort(key=lambda s: (s["file"], s.get("line") or 0))
    all_removed.sort(key=lambda s: (s["file"], s.get("line") or 0))
    all_modified.sort(key=lambda s: (s["file"], s.get("line") or 0))

    total_changes = (
        len(all_added) + len(all_removed) + len(all_modified)
        + len(all_imports_added) + len(all_imports_removed)
    )

    verdict = (
        f"{total_changes} structural change{'s' if total_changes != 1 else ''} "
        f"in {files_analyzed} file{'s' if files_analyzed != 1 else ''}"
    )

    # 3. Output
    if json_mode:
        click.echo(to_json(json_envelope(
            "semantic-diff",
            summary={
                "verdict": verdict,
                "files_changed": files_analyzed,
                "symbols_added": len(all_added),
                "symbols_removed": len(all_removed),
                "symbols_modified": len(all_modified),
                "imports_added": len(all_imports_added),
                "imports_removed": len(all_imports_removed),
            },
            base_ref=base_ref,
            symbols_added=all_added,
            symbols_removed=all_removed,
            symbols_modified=all_modified,
            imports_added=all_imports_added,
            imports_removed=all_imports_removed,
        )))
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    click.echo()

    if all_added:
        click.echo(f"SYMBOLS ADDED ({len(all_added)}):")
        for s in all_added:
            kind = abbrev_kind(s["kind"])
            location = loc(s["file"], s.get("line"))
            lines_info = f"  ({s['lines']} lines)" if s.get("lines") else ""
            click.echo(f"  + {kind} {s['name']}   at {location}{lines_info}")
        click.echo()

    if all_removed:
        click.echo(f"SYMBOLS REMOVED ({len(all_removed)}):")
        for s in all_removed:
            kind = abbrev_kind(s["kind"])
            location = loc(s["file"], s.get("line"))
            click.echo(f"  - {kind} {s['name']}   at {location}")
        click.echo()

    if all_modified:
        click.echo(f"SYMBOLS MODIFIED ({len(all_modified)}):")
        for s in all_modified:
            kind = abbrev_kind(s["kind"])
            location = loc(s["file"], s.get("line"))
            click.echo(f"  ~ {kind} {s['name']}   at {location}")
            changes = s.get("changes", {})
            if "params" in changes:
                p = changes["params"]
                param_details = []
                for added_p in p.get("added", []):
                    param_details.append(f"+{added_p}")
                for removed_p in p.get("removed", []):
                    param_details.append(f"-{removed_p}")
                param_str = ", ".join(param_details) if param_details else "reordered"
                click.echo(
                    f"    params: {param_str}  "
                    f"(was: {p['old_count']} params, now: {p['new_count']} params)"
                )
            if "body_lines" in changes:
                b = changes["body_lines"]
                click.echo(f"    body: {b['old']} -> {b['new']} lines")
        click.echo()

    if all_imports_added or all_imports_removed:
        total_imp = len(all_imports_added) + len(all_imports_removed)
        click.echo(f"IMPORTS CHANGED ({total_imp}):")
        for imp in all_imports_added:
            click.echo(f"  + {imp['file']} imports {imp['import']}")
        for imp in all_imports_removed:
            click.echo(f"  - {imp['file']} imports {imp['import']}")
        click.echo()

    # Summary line
    parts = []
    if all_added:
        parts.append(f"+{len(all_added)} symbols")
    if all_removed:
        parts.append(f"-{len(all_removed)} symbols")
    if all_modified:
        parts.append(f"~{len(all_modified)} modified")
    if all_imports_added or all_imports_removed:
        parts.append(
            f"+{len(all_imports_added)}/-{len(all_imports_removed)} imports"
        )
    click.echo(f"SUMMARY: {', '.join(parts)}")
