"""Context-enriched grep: text search annotated with enclosing symbols."""

import os
import re
import subprocess

import click

from roam.db.connection import find_project_root, open_db
from roam.output.formatter import abbrev_kind, loc, to_json
from roam.commands.resolve import ensure_index


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
@click.pass_context
def grep_cmd(ctx, pattern, glob_filter, count):
    """Context-enriched grep: search with enclosing symbol annotation."""
    json_mode = ctx.obj.get('json') if ctx.obj else False
    ensure_index()
    root = find_project_root()

    # Normalize shorthand extensions: "ts" or ".ts" → "*.ts"
    if glob_filter and "*" not in glob_filter and "?" not in glob_filter:
        ext = glob_filter if glob_filter.startswith(".") else f".{glob_filter}"
        glob_filter = f"*{ext}"

    matches = _grep_files(pattern, root, glob_filter)
    if not matches:
        if json_mode:
            click.echo(to_json({"pattern": pattern, "matches": []}))
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
            click.echo(to_json({"pattern": pattern, "total": len(matches), "matches": results}))
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
