"""Discover Architecture Decision Records (ADRs) and link them to code modules."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json

# ---------------------------------------------------------------------------
# ADR discovery
# ---------------------------------------------------------------------------

# Directories where ADRs are commonly stored
_ADR_DIRS = [
    "docs/adr",
    "docs/adrs",
    "doc/adr",
    "doc/adrs",
    "architecture/decisions",
    "adr",
    "adrs",
    "docs/architecture/decisions",
    "doc/architecture/decisions",
]

# Filename patterns that indicate an ADR (case-insensitive matching)
_ADR_FILE_PATTERNS = [
    re.compile(r"^\d{4}-.*\.md$", re.IGNORECASE),  # 0001-use-react.md
    re.compile(r"^adr-\d+.*\.md$", re.IGNORECASE),  # adr-001-use-react.md
    re.compile(r"^ADR-\d+.*\.md$"),  # ADR-001-use-react.md
    re.compile(r"^\d{4}_.*\.md$", re.IGNORECASE),  # 0001_use_react.md
    re.compile(r"^\d+-.*\.md$", re.IGNORECASE),  # 1-use-react.md (short number)
]

# Statuses recognized in ADR documents
_KNOWN_STATUSES = {
    "accepted",
    "proposed",
    "deprecated",
    "superseded",
    "rejected",
    "draft",
    "amended",
}


def _git_ls_files(project_root: Path) -> list[str] | None:
    """List tracked + untracked files via git ls-files."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return [p.strip() for p in result.stdout.splitlines() if p.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _discover_adr_files(project_root: Path) -> list[str]:
    """Find ADR files in the project.

    Strategy:
    1. Check well-known ADR directories for markdown files.
    2. Scan git ls-files for files matching ADR naming patterns
       outside of those directories (catches non-standard locations).

    Returns a deduplicated sorted list of relative paths (forward slashes).
    """
    found: set[str] = set()

    # Strategy 1: Well-known directories
    for adr_dir in _ADR_DIRS:
        full_dir = project_root / adr_dir
        if full_dir.is_dir():
            for entry in full_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() == ".md":
                    rel = entry.relative_to(project_root)
                    found.add(str(rel).replace("\\", "/"))

    # Strategy 2: Scan all files for ADR-like filenames
    all_files = _git_ls_files(project_root)
    if all_files is None:
        # Fallback: walk known dirs only (already done above)
        pass
    else:
        for rel_path in all_files:
            rel_path = rel_path.replace("\\", "/")
            basename = os.path.basename(rel_path)
            if not basename.lower().endswith(".md"):
                continue
            for pattern in _ADR_FILE_PATTERNS:
                if pattern.match(basename):
                    found.add(rel_path)
                    break

    return sorted(found)


# ---------------------------------------------------------------------------
# ADR parsing
# ---------------------------------------------------------------------------

# YAML frontmatter fence
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Simple YAML key: value extractor (avoids PyYAML dependency)
_YAML_KV_RE = re.compile(r"^(\w[\w-]*)\s*:\s*(.+)$", re.MULTILINE)

# Heading patterns
_TITLE_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)

# Status patterns in body text (e.g., "Status: Accepted" or "## Status\nAccepted")
_STATUS_HEADING_RE = re.compile(r"^#+\s*status\s*\n+\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_STATUS_INLINE_RE = re.compile(r"(?:^|\n)\s*\*?\*?status\*?\*?\s*:\s*(\w+)", re.IGNORECASE)

# Date patterns
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Module/file references: paths ending in common source extensions, or
# dotted module names that look like qualified Python/Java names
_FILE_REF_RE = re.compile(
    r"(?:`([^`]+\.\w{1,5})`"  # backtick-quoted file paths
    r"|(?:^|\s)([\w/\\.-]+\.(?:py|js|ts|go|java|rs|rb|cpp|c|cs|php|kt|swift|scala)))"
    r"",
    re.MULTILINE,
)
_MODULE_REF_RE = re.compile(
    r"(?:`([\w.]+)`)"  # backtick-quoted dotted names
)


def _parse_simple_yaml(text: str) -> dict[str, str]:
    """Extract key-value pairs from simple YAML frontmatter."""
    result = {}
    for m in _YAML_KV_RE.finditer(text):
        key = m.group(1).lower().strip()
        value = m.group(2).strip().strip('"').strip("'")
        result[key] = value
    return result


def _extract_status(frontmatter: dict[str, str], body: str) -> str:
    """Extract ADR status from frontmatter or body text."""
    # Check frontmatter first
    fm_status = frontmatter.get("status", "").lower().strip()
    if fm_status in _KNOWN_STATUSES:
        return fm_status

    # Check for "## Status" heading followed by status word
    m = _STATUS_HEADING_RE.search(body)
    if m:
        candidate = m.group(1).lower().strip()
        if candidate in _KNOWN_STATUSES:
            return candidate

    # Check for "Status: accepted" inline
    m = _STATUS_INLINE_RE.search(body)
    if m:
        candidate = m.group(1).lower().strip()
        if candidate in _KNOWN_STATUSES:
            return candidate

    return "unknown"


def _extract_title(frontmatter: dict[str, str], body: str, filename: str) -> str:
    """Extract ADR title from frontmatter, first heading, or filename."""
    # Frontmatter title
    fm_title = frontmatter.get("title", "").strip()
    if fm_title:
        return fm_title

    # First markdown heading
    m = _TITLE_RE.search(body)
    if m:
        title = m.group(1).strip()
        # Strip ADR number prefix if present (e.g., "ADR-001: Use React")
        title = re.sub(r"^(?:ADR[-\s]*)?0*\d+[.:)\s-]+\s*", "", title, flags=re.IGNORECASE)
        if title:
            return title

    # Derive from filename
    name = os.path.splitext(filename)[0]
    # Remove numeric prefix
    name = re.sub(r"^(?:adr[-_]?)?\d+[-_]?", "", name, flags=re.IGNORECASE)
    return name.replace("-", " ").replace("_", " ").strip().title() or filename


def _extract_date(frontmatter: dict[str, str], body: str) -> str | None:
    """Extract date from frontmatter or body."""
    for key in ("date", "created", "last-modified", "last_modified"):
        val = frontmatter.get(key, "").strip()
        m = _DATE_RE.search(val)
        if m:
            return m.group(1)

    # First date found in body
    m = _DATE_RE.search(body)
    if m:
        return m.group(1)

    return None


def _extract_file_refs(body: str) -> list[str]:
    """Extract file/module references from ADR body text."""
    refs: set[str] = set()

    for m in _FILE_REF_RE.finditer(body):
        ref = m.group(1) or m.group(2)
        if ref:
            ref = ref.strip().replace("\\", "/")
            # Skip very short or URL-like refs
            if len(ref) > 3 and "://" not in ref:
                refs.add(ref)

    for m in _MODULE_REF_RE.finditer(body):
        ref = m.group(1).strip()
        # Must have at least one dot and look like a module path
        if "." in ref and len(ref) > 5 and not ref.startswith("http"):
            # Skip version numbers and IPs
            if not re.match(r"^\d+\.\d+", ref):
                refs.add(ref)

    return sorted(refs)


def _parse_adr(project_root: Path, rel_path: str) -> dict | None:
    """Parse a single ADR file and extract metadata.

    Returns a dict with keys: path, title, status, date, file_refs, number.
    Returns None if the file cannot be read.
    """
    full_path = project_root / rel_path
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Parse frontmatter
    frontmatter: dict[str, str] = {}
    body = content
    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        frontmatter = _parse_simple_yaml(fm_match.group(1))
        body = content[fm_match.end() :]

    filename = os.path.basename(rel_path)

    # Extract ADR number from filename
    num_match = re.match(r"(?:adr[-_]?)?(\d+)", filename, re.IGNORECASE)
    number = int(num_match.group(1)) if num_match else None

    title = _extract_title(frontmatter, body, filename)
    status = _extract_status(frontmatter, body)
    date = _extract_date(frontmatter, body)
    file_refs = _extract_file_refs(body)

    return {
        "path": rel_path,
        "number": number,
        "title": title,
        "status": status,
        "date": date,
        "file_refs": file_refs,
    }


# ---------------------------------------------------------------------------
# Cross-reference with symbol table
# ---------------------------------------------------------------------------


def _resolve_code_modules(conn, adrs: list[dict]) -> list[dict]:
    """Enrich ADR records with linked code modules from the symbol table.

    For each ADR, checks its file_refs against:
    1. File paths in the DB (exact or suffix match)
    2. Symbol qualified names (prefix match for module refs)

    Adds a 'linked_modules' list to each ADR dict.
    """
    # Build lookup structures from DB
    file_rows = conn.execute("SELECT id, path FROM files").fetchall()
    file_paths = {r["path"]: r["id"] for r in file_rows}
    # Basename -> list of full paths for fuzzy matching
    basename_map: dict[str, list[str]] = {}
    for path in file_paths:
        bn = os.path.basename(path)
        basename_map.setdefault(bn, []).append(path)

    # Symbol qualified names for module matching
    sym_rows = conn.execute(
        "SELECT DISTINCT s.qualified_name, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.qualified_name IS NOT NULL AND s.qualified_name != '' "
        "LIMIT 10000"
    ).fetchall()
    qname_to_file: dict[str, str] = {}
    for r in sym_rows:
        qname_to_file[r["qualified_name"]] = r["path"]

    for adr in adrs:
        linked: set[str] = set()

        for ref in adr.get("file_refs", []):
            # Direct path match
            if ref in file_paths:
                linked.add(ref)
                continue

            # Suffix match (e.g., "parser.py" matches "src/roam/index/parser.py")
            ref_basename = os.path.basename(ref)
            candidates = basename_map.get(ref_basename, [])
            if candidates:
                # Prefer the candidate whose path ends with the full ref
                for c in candidates:
                    if c.endswith(ref):
                        linked.add(c)
                        break
                else:
                    # Fall back to first basename match
                    linked.add(candidates[0])
                continue

            # Module qualified name prefix match
            # e.g., "roam.index.parser" might match symbol qnames
            for qname, fpath in qname_to_file.items():
                if qname.startswith(ref) or ref.startswith(qname):
                    linked.add(fpath)
                    break

        adr["linked_modules"] = sorted(linked)

    return adrs


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="adrs",
    category="getting-started",
    summary="Discover Architecture Decision Records and link them to code modules",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("adrs")
@click.option(
    "--status",
    "filter_status",
    default=None,
    help="Filter ADRs by status (e.g., accepted, deprecated, superseded).",
)
@click.option(
    "--limit",
    default=50,
    show_default=True,
    help="Maximum number of ADRs to display.",
)
@click.pass_context
def adrs(ctx, filter_status, limit):
    """Discover Architecture Decision Records and link them to code modules.

    Scans the project for ADR files in common locations (docs/adr/,
    architecture/decisions/, etc.), parses their metadata (title, status,
    date), and cross-references mentioned files/modules with the symbol
    index.

    Unlike ``doc-staleness`` (which measures inline docstring drift),
    this command focuses on prose decision documents and their linkage
    to live code.

    Use --status to filter by ADR status:

        roam adrs                   # all ADRs
        roam adrs --status accepted # only accepted
        roam adrs --status deprecated
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    # Discover ADR files
    adr_files = _discover_adr_files(project_root)

    if not adr_files:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "adrs",
                        summary={
                            "verdict": "no ADRs found",
                            "adr_count": 0,
                            "linked_count": 0,
                        },
                        adrs=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: No Architecture Decision Records found")
            click.echo()
            click.echo("  Searched directories: " + ", ".join(_ADR_DIRS[:5]))
            click.echo("  Searched patterns: NNNN-*.md, adr-*.md, ADR-*.md")
            click.echo()
            click.echo("  To create ADRs, consider using a tool like adr-tools")
            click.echo("  or create markdown files in docs/adr/ following the")
            click.echo("  Nygard or MADR format.")
        return

    # Parse each ADR
    parsed: list[dict] = []
    for rel_path in adr_files:
        record = _parse_adr(project_root, rel_path)
        if record:
            parsed.append(record)

    # Filter by status if requested
    if filter_status:
        filter_lower = filter_status.lower().strip()
        parsed = [a for a in parsed if a["status"] == filter_lower]

    if not parsed:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "adrs",
                        summary={
                            "verdict": f"no ADRs with status '{filter_status}'",
                            "adr_count": 0,
                            "linked_count": 0,
                            "filter_status": filter_status,
                        },
                        adrs=[],
                    )
                )
            )
        else:
            click.echo(f"No ADRs found with status '{filter_status}'.")
            click.echo(f"  Found {len(adr_files)} ADR file(s) total.")
        return

    # Sort by number (if available), then by path
    parsed.sort(key=lambda a: (a["number"] if a["number"] is not None else 99999, a["path"]))

    # Cross-reference with code modules
    with open_db(readonly=True) as conn:
        parsed = _resolve_code_modules(conn, parsed)

    # Apply limit
    total_count = len(parsed)
    displayed = parsed[:limit]

    # Compute summary stats
    status_counts: dict[str, int] = {}
    linked_count = 0
    for a in parsed:
        s = a["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
        if a.get("linked_modules"):
            linked_count += 1

    # Verdict
    if total_count == 0:
        verdict = "no ADRs found"
    elif linked_count == total_count:
        verdict = f"{total_count} ADR(s), all linked to code"
    elif linked_count > 0:
        verdict = f"{total_count} ADR(s), {linked_count} linked to code"
    else:
        verdict = f"{total_count} ADR(s), none linked to code"

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "adrs",
                    summary={
                        "verdict": verdict,
                        "adr_count": total_count,
                        "displayed": len(displayed),
                        "linked_count": linked_count,
                        "status_counts": status_counts,
                        "filter_status": filter_status,
                    },
                    adrs=[
                        {
                            "number": a["number"],
                            "title": a["title"],
                            "status": a["status"],
                            "date": a["date"],
                            "path": a["path"],
                            "file_refs": a["file_refs"],
                            "linked_modules": a["linked_modules"],
                        }
                        for a in displayed
                    ],
                )
            )
        )
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}\n")

    # Summary table
    headers = ["#", "Title", "Status", "Date", "Linked"]
    rows = []
    for a in displayed:
        num_str = str(a["number"]) if a["number"] is not None else "-"
        title = a["title"]
        if len(title) > 45:
            title = title[:42] + "..."
        date_str = a["date"] or "-"
        linked_str = str(len(a["linked_modules"])) if a["linked_modules"] else "-"
        rows.append([num_str, title, a["status"], date_str, linked_str])

    click.echo(format_table(headers, rows))
    click.echo()

    # Show linked modules detail for ADRs that have them
    linked_adrs = [a for a in displayed if a.get("linked_modules")]
    if linked_adrs:
        click.echo("LINKED MODULES:")
        for a in linked_adrs:
            num_str = f"ADR-{a['number']}" if a["number"] is not None else a["path"]
            click.echo(f"  {num_str}: {a['title']}")
            for mod in a["linked_modules"][:5]:
                click.echo(f"    -> {mod}")
            if len(a["linked_modules"]) > 5:
                click.echo(f"    (+{len(a['linked_modules']) - 5} more)")
        click.echo()

    # Status summary
    if len(status_counts) > 1:
        click.echo("STATUS BREAKDOWN:")
        for status, count in sorted(status_counts.items()):
            click.echo(f"  {status:<15s} {count}")
        click.echo()

    if total_count > limit:
        click.echo(f"  (+{total_count - limit} more ADRs, use --limit to see all)")
