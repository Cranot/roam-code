"""Context-enriched grep: text search annotated with enclosing symbols."""

from __future__ import annotations

import os
import re
import subprocess

import click

from roam.db.connection import find_project_root, open_db
from roam.output.formatter import abbrev_kind, loc, to_json, json_envelope
from roam.commands.resolve import ensure_index
from roam.commands.changed_files import is_test_file
from roam.index.file_roles import classify_file, ROLE_SOURCE, ROLE_TEST


# ---------------------------------------------------------------------------
# Source-only exclusion patterns
# ---------------------------------------------------------------------------

_SOURCE_ONLY_EXCLUDES = [
    "*.md", "*.markdown", "*.txt", "*.rst",
    "*.json", "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg",
    "*.lock", "*.example", "*.sample",
    "*.svg", "*.png", "*.jpg", "*.gif", "*.ico",
    "docs/**", "**/docs/**",
]


def _matches_any_exclude(path, excludes):
    """Check if a path matches any of the exclusion patterns."""
    import fnmatch
    p = path.replace("\\", "/")
    for pat in excludes:
        if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(os.path.basename(p), pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Core grep
# ---------------------------------------------------------------------------


def _grep_files(pattern, root, glob_filter=None):
    """Grep for a pattern using git grep (fast) or fallback to manual search."""
    matches = []
    regex = re.compile(pattern, re.IGNORECASE)

    # Try git grep first
    try:
        cmd = ["git", "grep", "-n", "-I", "--no-color", "-E", pattern]
        if glob_filter:
            cmd.extend(["--", glob_filter])
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        if result.returncode <= 1:  # 0 = matches, 1 = no matches
            for line in result.stdout.splitlines():
                # Format: path:line_num:content
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    path, line_num, content = parts[0], parts[1], parts[2]
                    try:
                        matches.append({
                            "path": path.replace("\\", "/"),
                            "line": int(line_num),
                            "content": content.strip(),
                        })
                    except ValueError:
                        continue
            return matches
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: manual file search using indexed files
    try:
        with open_db(readonly=True) as conn:
            rows = conn.execute("SELECT path FROM files").fetchall()
            file_paths = [r["path"] for r in rows]
    except Exception:
        return matches

    for rel_path in file_paths:
        if glob_filter and not _matches_glob(rel_path, glob_filter):
            continue
        full_path = root / rel_path
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append({
                    "path": rel_path,
                    "line": i,
                    "content": line.strip(),
                })

    return matches


def _matches_glob(path, pattern):
    """Simple glob matching."""
    import fnmatch
    return fnmatch.fnmatch(path, pattern)


def _find_enclosing_symbol(conn, file_path, line_num):
    """Find the most specific symbol that contains a given line."""
    row = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if not row:
        return None

    file_id = row["id"]

    # Find the most specific (smallest range) symbol containing this line
    result = conn.execute(
        """SELECT name, qualified_name, kind, line_start, line_end
           FROM symbols
           WHERE file_id = ? AND line_start <= ? AND line_end >= ?
           ORDER BY (line_end - line_start) ASC
           LIMIT 1""",
        (file_id, line_num, line_num),
    ).fetchone()

    if result:
        return {
            "name": result["name"],
            "qualified_name": result["qualified_name"],
            "kind": result["kind"],
            "line_start": result["line_start"],
        }
    return None


@click.command("grep")
@click.argument("pattern")
@click.option("-g", "--glob", "glob_filter", default=None,
              help="Filter by file type or glob (e.g. 'vue', '.ts', '*.php')")
@click.option("-n", "count", default=50, help="Max results to show")
@click.option("-s", "--source-only", is_flag=True,
              help="Exclude docs, configs, and non-source files")
@click.option("-t", "--test-only", is_flag=True,
              help="Only search in test files")
@click.option("--exclude", "exclude_patterns", default=None,
              help="Comma-separated exclusion globs (e.g. '*.md,docs/**')")
@click.pass_context
def grep_cmd(ctx, pattern, glob_filter, count, source_only, test_only, exclude_patterns):
    """Context-enriched grep: search with enclosing symbol annotation."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # Normalize shorthand extensions: "ts" or ".ts" → "*.ts"
    if glob_filter and "*" not in glob_filter and "?" not in glob_filter:
        ext = glob_filter if glob_filter.startswith(".") else f".{glob_filter}"
        glob_filter = f"*{ext}"

    matches = _grep_files(pattern, root, glob_filter)

    # --- Apply post-filters ---
    excludes = []
    if source_only:
        excludes.extend(_SOURCE_ONLY_EXCLUDES)
    if exclude_patterns:
        excludes.extend(p.strip() for p in exclude_patterns.split(",") if p.strip())

    if excludes:
        matches = [m for m in matches if not _matches_any_exclude(m["path"], excludes)]

    # Use file_roles classifier for smarter source-only / test-only filtering
    if source_only:
        matches = [m for m in matches
                   if classify_file(m["path"]) in (ROLE_SOURCE, ROLE_TEST)]

    if test_only:
        matches = [m for m in matches if is_test_file(m["path"])]

    if not matches:
        if json_mode:
            click.echo(to_json(json_envelope("grep",
                summary={"total": 0},
                pattern=pattern, matches=[],
                source_only=source_only, test_only=test_only,
            )))
        else:
            click.echo(f"No matches for '{pattern}'")
        return

    if json_mode:
        with open_db(readonly=True) as conn:
            results = []
            for m in matches[:count]:
                sym = _find_enclosing_symbol(conn, m["path"], m["line"])
                entry = {"path": m["path"], "line": m["line"], "content": m["content"]}
                if sym:
                    entry["enclosing_symbol"] = sym["qualified_name"]
                    entry["enclosing_kind"] = sym["kind"]
                results.append(entry)
            click.echo(to_json(json_envelope("grep",
                summary={"total": len(matches), "shown": len(results)},
                pattern=pattern,
                total=len(matches),
                source_only=source_only,
                test_only=test_only,
                exclude_patterns=excludes if excludes else None,
                matches=results,
            )))
        return

    click.echo(f"=== {len(matches)} matches for '{pattern}' ===\n")

    with open_db(readonly=True) as conn:
        shown = 0
        for m in matches:
            if shown >= count:
                remaining = len(matches) - count
                click.echo(f"\n(+{remaining} more)")
                break

            sym = _find_enclosing_symbol(conn, m["path"], m["line"])
            location = loc(m["path"], m["line"])

            if sym:
                sym_info = f"  in {abbrev_kind(sym['kind'])} {sym['qualified_name']}"
            else:
                sym_info = ""

            # Truncate content for readability
            content = m["content"]
            if len(content) > 100:
                content = content[:97] + "..."

            click.echo(f"  {location}{sym_info}")
            click.echo(f"    {content}")
            shown += 1
