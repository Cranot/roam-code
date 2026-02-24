"""Tree-sitter syntax validation for multi-agent Judge pattern.

Parses files with tree-sitter and reports ERROR / MISSING nodes in the AST.
Works WITHOUT a roam index -- parses files directly.

Exit codes:
  0  All files are syntactically clean.
  5  One or more files contain syntax errors (EXIT_GATE_FAILURE).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from roam.index.parser import (
    GRAMMAR_ALIASES,
    REGEX_ONLY_LANGUAGES,
    detect_language,
    read_source,
)
from roam.output.formatter import to_json, json_envelope


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _extract_error_context(source: bytes, node) -> str:
    """Extract a human-readable snippet around an ERROR / MISSING node."""
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return "(unreadable)"

    lines = text.split("\n")
    start_line = node.start_point[0]
    end_line = node.end_point[0]

    if node.is_missing:
        # MISSING nodes have zero width -- describe what was expected
        expected = node.type.replace("MISSING", "").strip() or "token"
        return f"missing {expected}"

    # For ERROR nodes, grab the offending source text (max 120 chars)
    if start_line == end_line and start_line < len(lines):
        line_text = lines[start_line].rstrip()
        col_start = node.start_point[1]
        col_end = node.end_point[1]
        snippet = line_text[col_start:col_end]
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        return snippet if snippet else line_text.strip()[:120]

    # Multi-line error: show first line context
    if start_line < len(lines):
        first_line = lines[start_line].rstrip()
        n_lines = end_line - start_line + 1
        snippet = first_line[node.start_point[1]:]
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        return f"{snippet} (+{n_lines - 1} more lines)"

    return "(syntax error)"


def check_syntax(file_path: str, source: bytes, tree) -> list[dict]:
    """Walk a tree-sitter AST and collect ERROR / MISSING nodes.

    Returns a list of error dicts with line, column, end_line, end_col,
    node_type, and text fields.
    """
    errors: list[dict] = []
    if tree is None:
        return errors

    def _visit(node):
        if node.type == "ERROR" or node.is_missing:
            line = node.start_point[0] + 1
            col = node.start_point[1] + 1
            errors.append({
                "line": line,
                "column": col,
                "end_line": node.end_point[0] + 1,
                "end_col": node.end_point[1] + 1,
                "node_type": "MISSING" if node.is_missing else "ERROR",
                "text": _extract_error_context(source, node),
            })
        for child in node.children:
            _visit(child)

    _visit(tree.root_node)
    return errors


def _parse_file_for_syntax(file_path: str) -> dict | None:
    """Parse a single file with tree-sitter and return error info.

    Returns None if the file type is unsupported or unparseable.
    Returns a dict with path, language, and errors list.
    """
    language = detect_language(file_path)
    if language is None:
        return None

    # Regex-only languages have no tree-sitter grammar -- skip
    if language in REGEX_ONLY_LANGUAGES:
        return None

    path = Path(file_path)
    source = read_source(path)
    if source is None:
        return None

    # Resolve grammar alias
    grammar = GRAMMAR_ALIASES.get(language, language)

    try:
        from tree_sitter_language_pack import get_parser
        parser = get_parser(grammar)
    except Exception:
        return None

    try:
        tree = parser.parse(source)
    except Exception:
        return None

    errors = check_syntax(file_path, source, tree)
    return {
        "path": file_path.replace("\\", "/"),
        "language": language,
        "errors": errors,
    }


def _get_changed_files() -> list[str]:
    """Get files changed in the working tree (unstaged + staged) via git."""
    try:
        # Unstaged changes
        r1 = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        # Staged changes
        r2 = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        # Untracked new files
        r3 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        files: set[str] = set()
        for r in (r1, r2, r3):
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        files.add(line.replace("\\", "/"))
        return sorted(files)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("syntax-check")
@click.argument("paths", nargs=-1)
@click.option("--changed", is_flag=True,
              help="Check only git-changed files (unstaged + staged + untracked).")
@click.pass_context
def syntax_check(ctx, paths, changed):
    """Check files for syntax errors using tree-sitter AST parsing.

    Parses each file with tree-sitter and walks the AST looking for ERROR
    and MISSING nodes.  Works without a roam index -- parses files directly.

    \b
    Exit codes:
      0  All files are syntactically clean.
      5  One or more files contain syntax errors.

    \b
    Examples:
      roam syntax-check src/app.py src/lib.js
      roam syntax-check --changed
      roam --json syntax-check --changed
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Resolve file list
    if changed:
        file_list = _get_changed_files()
    elif paths:
        file_list = list(paths)
    else:
        click.echo("Error: provide file paths or use --changed.", err=True)
        ctx.exit(2)
        return

    # Filter to files that actually exist
    file_list = [p for p in file_list if os.path.isfile(p)]

    if not file_list:
        if json_mode:
            click.echo(to_json(json_envelope("syntax-check",
                summary={
                    "verdict": "No files to check",
                    "total_files": 0,
                    "files_with_errors": 0,
                    "total_errors": 0,
                    "clean": True,
                },
                files=[],
            )))
        else:
            click.echo("VERDICT: No files to check")
        return

    # Parse each file
    results: list[dict] = []
    skipped = 0
    for fp in file_list:
        result = _parse_file_for_syntax(fp)
        if result is None:
            skipped += 1
            continue
        results.append(result)

    total_files = len(results)
    files_with_errors = [r for r in results if r["errors"]]
    total_errors = sum(len(r["errors"]) for r in results)
    clean = total_errors == 0

    if clean:
        verdict = f"clean -- {total_files} files checked, 0 errors"
    else:
        n_err_files = len(files_with_errors)
        verdict = (
            f"{total_errors} syntax error{'s' if total_errors != 1 else ''} "
            f"in {n_err_files} file{'s' if n_err_files != 1 else ''}"
        )

    if json_mode:
        click.echo(to_json(json_envelope("syntax-check",
            summary={
                "verdict": verdict,
                "total_files": total_files,
                "files_with_errors": len(files_with_errors),
                "total_errors": total_errors,
                "clean": clean,
            },
            files=[r for r in results if r["errors"]],
        )))
    else:
        click.echo(f"VERDICT: {verdict}")
        if files_with_errors:
            click.echo("")
            for r in files_with_errors:
                for err in r["errors"]:
                    node_label = err["node_type"]
                    click.echo(
                        f"  {r['path']}:{err['line']}:{err['column']}  "
                        f"{node_label}  {err['text']}"
                    )
            click.echo("")
            click.echo(
                f"{total_errors} errors, "
                f"{len(files_with_errors)} files affected, "
                f"{total_files} files checked"
            )

    if not clean:
        from roam.exit_codes import EXIT_GATE_FAILURE
        ctx.exit(EXIT_GATE_FAILURE)
