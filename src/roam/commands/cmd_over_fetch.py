"""Detect API endpoints that return more data than necessary.

Finds models with many fields that are fully serialized when only a few
fields are needed — the "over-fetch" anti-pattern that bloats API responses
with unused columns, wastes bandwidth, and leaks internal schema details.

Detection checks:

1. **Large models without $hidden** — Models with many $fillable fields
   (>threshold) that don't define ``$hidden`` to suppress serialized output.
   Every API response includes ALL fillable fields unless hidden or visible is set.

2. **Models without API Resources** — Models returned directly from controllers
   via ``response()->json($model)`` instead of through a Laravel API Resource
   that controls which fields are exposed.

3. **$fillable count vs $hidden count ratio** — If a model has 50 fillable
   fields and only hides 2, the API response is still 48 fields.

4. **Missing select() in queries** — Controllers/services that do
   ``Model::all()`` or ``Model::paginate()`` without ``->select(['f1', 'f2'])``
   to limit returned columns, especially on large models.

Confidence levels:

- ``high``   — 30+ fillable fields, no $hidden, no $visible, direct controller return
- ``medium`` — 20+ fillable fields, minimal $hidden (< 3 fields hidden)
- ``low``    — 15+ fillable fields, no select() optimization in queries

Supported frameworks: Laravel/Eloquent (PHP).
"""

from __future__ import annotations

from collections import defaultdict
import re
import os

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import loc, to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to extract entries from PHP array literals like:
#   'field1', 'field2', "field3", ...
_ARRAY_STRING_RE = re.compile(r"""['"]([^'"]+)['"]""")

# Patterns that indicate a model field is being directly returned to the client
# without going through an API Resource.
_DIRECT_RETURN_PATTERNS = [
    # return $model;  /  return $record;  /  return $this->model;
    re.compile(r'\breturn\s+\$\w+\s*;'),
    # response()->json($model)  /  response()->json($record)
    re.compile(r'response\s*\(\s*\)\s*->\s*json\s*\(\s*\$\w+'),
    # ->json($model)
    re.compile(r'->\s*json\s*\(\s*\$\w+'),
]

# Patterns that indicate an API Resource wrapping (these are safe)
_RESOURCE_PATTERNS = [
    # new SomeResource($model)  /  SomeResource::collection(...)
    re.compile(r'\bnew\s+\w+Resource\s*\('),
    re.compile(r'\w+Resource\s*::\s*collection\s*\('),
    # JsonResource, AnonymousResourceCollection
    re.compile(r'\bJsonResource\b'),
]

# select() call detection — indicates developer is intentionally limiting columns
_SELECT_CALL_RE = re.compile(r'->\s*select\s*\(')

# Unoptimized query patterns (no select)
_UNOPTIMIZED_QUERY_RE = re.compile(
    r'(?:Model::all\(\)|Model::paginate\(|'
    r'::\s*all\s*\(\s*\)|::\s*paginate\s*\(|'
    r'::\s*get\s*\(\s*\))',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = os.path.basename(p)
    if base.startswith("test_") or base.endswith("_test.php"):
        return True
    if "/tests/" in p or "/test/" in p or "/testing/" in p:
        return True
    return False


def _read_file_lines(abs_path) -> list[str]:
    """Read file lines safely, returning empty list on failure."""
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _extract_array_fields(source: str, property_name: str) -> list[str]:
    """Extract string values from a PHP class property array.

    Handles both ``$fillable = ['a', 'b']`` and ``$fillable = ["a", "b"]``
    across multiple lines.

    Returns list of field name strings found, or empty list if property absent.
    """
    # Match:  $fillable = [ ... ]  or  protected $fillable = [ ... ];
    # Uses a non-greedy match that stops at the closing bracket.
    pattern = re.compile(
        rf'\$\s*{re.escape(property_name)}\s*=\s*\[([^\]]*)\]',
        re.DOTALL,
    )
    m = pattern.search(source)
    if not m:
        return []
    return _ARRAY_STRING_RE.findall(m.group(1))


def _has_visible_property(source: str) -> bool:
    """Return True if the model defines $visible (whitelist — fully controlled)."""
    return bool(re.search(r'\$\s*visible\s*=\s*\[', source))


def _find_api_resource_for_model(conn, model_name: str) -> str | None:
    """Find an API Resource file matching this model name.

    Looks for files named <ModelName>Resource.php in the codebase.
    Returns the file path string, or None if not found.
    """
    resource_name = f"{model_name}Resource.php"
    rows = conn.execute(
        "SELECT path FROM files WHERE path LIKE ?",
        (f"%{resource_name}",),
    ).fetchall()
    for row in rows:
        p = row["path"].replace("\\", "/")
        if "Resource" in p or "resource" in p:
            return row["path"]
    # Also check for collection resources
    collection_name = f"{model_name}Collection.php"
    rows2 = conn.execute(
        "SELECT path FROM files WHERE path LIKE ?",
        (f"%{collection_name}",),
    ).fetchall()
    if rows2:
        return rows2[0]["path"]
    return None


def _count_resource_fields(root, resource_path: str) -> int | None:
    """Count fields exposed in a Laravel API Resource's ``toArray()`` method.

    Parses the ``return [...]`` array inside ``toArray()`` and counts
    ``'key' =>`` patterns.  Returns the field count, or ``None`` if the
    method cannot be parsed (e.g. dynamic or delegated responses).
    """
    abs_path = root / resource_path
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    m = re.search(r'function\s+toArray\s*\(', source)
    if not m:
        return None

    rest = source[m.end():]
    return_match = re.search(r'return\s*\[', rest)
    if not return_match:
        return None

    bracket_pos = rest.index('[', return_match.start())
    depth = 0
    pos = bracket_pos
    while pos < len(rest):
        if rest[pos] == '[':
            depth += 1
        elif rest[pos] == ']':
            depth -= 1
            if depth == 0:
                break
        pos += 1

    array_body = rest[bracket_pos:pos + 1]

    # Each 'key' => represents one exposed field in the response
    return len(re.findall(r"""['\"][^'\"]+['\"]\s*=>""", array_body))


def _check_controller_direct_returns(
    conn,
    model_name: str,
    root,
) -> list[dict]:
    """Check controller files for direct model returns (without API Resources).

    Returns list of locations where the model is returned directly.
    """
    direct_returns = []

    controller_files = conn.execute(
        "SELECT path FROM files "
        "WHERE (path LIKE '%Controller%' OR path LIKE '%controller%') "
        "AND path LIKE '%.php'",
    ).fetchall()

    for row in controller_files:
        if _is_test_path(row["path"]):
            continue
        # Skip console commands and non-HTTP controllers
        p_lower = row["path"].replace("\\", "/").lower()
        if "/console/" in p_lower or "/commands/" in p_lower:
            continue
        # Only match files in HTTP controller directories
        if "controller" in os.path.basename(row["path"]).lower() and \
           "/http/" not in p_lower and "/controllers/" not in p_lower:
            continue
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Only check controllers that reference this model
        if model_name not in content:
            continue

        lines = content.splitlines()
        for line_no, line in enumerate(lines, start=1):
            # Skip if the line uses a Resource (safe pattern)
            if any(p.search(line) for p in _RESOURCE_PATTERNS):
                continue
            # Flag direct returns
            for pattern in _DIRECT_RETURN_PATTERNS:
                if pattern.search(line):
                    direct_returns.append({
                        "file": row["path"],
                        "line": line_no,
                        "location": loc(row["path"], line_no),
                        "snippet": line.strip()[:100],
                    })
                    break  # one match per line is enough

    return direct_returns


def _check_missing_select(
    conn,
    model_name: str,
    root,
) -> list[dict]:
    """Check controller/service files for queries without select().

    Returns list of locations where Model::all() or paginate() is used
    without a preceding ->select() call to limit columns.
    """
    missing_select = []

    # Look in controllers and services
    files = conn.execute(
        "SELECT path FROM files "
        "WHERE path LIKE '%.php' "
        "AND (path LIKE '%Controller%' OR path LIKE '%controller%' "
        "  OR path LIKE '%Service%' OR path LIKE '%service%' "
        "  OR path LIKE '%Repository%' OR path LIKE '%repository%')",
    ).fetchall()

    # Pattern to match Model::query(), Model::all(), Model::paginate() etc.
    # We build model-specific patterns
    model_query_re = re.compile(
        rf'{re.escape(model_name)}\s*::\s*(?:all|paginate|get|query|where|with)\s*\(',
    )

    for row in files:
        if _is_test_path(row["path"]):
            continue
        abs_path = root / row["path"]
        if not abs_path.is_file():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if model_name not in content:
            continue

        lines = content.splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not model_query_re.search(line):
                continue
            # Check if this line or the surrounding ±5 lines contain ->select(
            context_start = max(0, line_no - 6)
            context_end = min(len(lines), line_no + 5)
            context_block = "\n".join(lines[context_start:context_end])
            if _SELECT_CALL_RE.search(context_block):
                continue  # select() is nearby — OK
            missing_select.append({
                "file": row["path"],
                "line": line_no,
                "location": loc(row["path"], line_no),
                "snippet": line.strip()[:100],
            })

    return missing_select


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _find_model_files(conn) -> list[dict]:
    """Find PHP model files by path and presence of $fillable."""
    rows = conn.execute(
        "SELECT f.id as file_id, f.path, s.id as class_id, "
        "s.name as class_name, s.line_start, s.line_end "
        "FROM files f "
        "JOIN symbols s ON s.file_id = f.id "
        "WHERE s.kind = 'class' "
        "AND f.path LIKE '%.php' "
        "AND ("
        "  f.path LIKE '%/Models/%' "
        "  OR f.path LIKE '%\\\\Models\\\\%' "
        "  OR f.path LIKE '%/Model/%' "
        ") "
        "ORDER BY f.path",
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_over_fetch(conn, threshold: int, limit: int) -> list[dict]:
    """Run the over-fetch detection analysis.

    Returns list of finding dicts sorted by severity (high → medium → low).
    """
    root = find_project_root()
    findings = []

    model_files = _find_model_files(conn)

    for model_info in model_files:
        if _is_test_path(model_info["path"]):
            continue

        abs_path = root / model_info["path"]
        if not abs_path.is_file():
            continue

        source = ""
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Extract $fillable fields
        fillable = _extract_array_fields(source, "fillable")
        fillable_count = len(fillable)

        # Skip models with fewer fields than the minimum threshold
        if fillable_count < threshold:
            continue

        # Extract $hidden fields
        hidden = _extract_array_fields(source, "hidden")
        hidden_count = len(hidden)

        # Check for $visible (whitelist — strong protection)
        has_visible = _has_visible_property(source)

        # Check for a matching API Resource
        resource_path = _find_api_resource_for_model(conn, model_info["class_name"])
        has_resource = resource_path is not None

        # If a Resource exists, check how many fields it actually exposes.
        # When the Resource returns significantly fewer fields than the model
        # has, the output is well-filtered and the finding can be skipped.
        resource_field_count = None
        if has_resource and resource_path:
            resource_field_count = _count_resource_fields(root, resource_path)
            if resource_field_count is not None and resource_field_count < fillable_count * 0.5:
                continue  # Resource properly filters output

        # Effective exposed fields = fillable minus hidden (when no $visible)
        exposed_count = fillable_count - hidden_count if not has_visible else 0

        # -------------------------------------------------------------------
        # Determine confidence level
        # -------------------------------------------------------------------
        confidence = None
        reasons = []
        suggestions = []

        if has_visible:
            # $visible whitelist is the strictest protection — skip
            continue

        if fillable_count >= 30 and hidden_count == 0 and not has_resource:
            confidence = "high"
            reasons.append(
                f"Serializes {fillable_count} fields per item in list APIs"
            )
            if not has_resource:
                reasons.append("No API Resource found to control output")
            suggestions.append(
                f"Add $hidden for unused fields, or use ->select() in queries,\n"
                f"               or create a {model_info['class_name']}Resource "
                f"to control output\n"
                f"               Note: $hidden/$visible also hides fields from edit endpoints.\n"
                f"               For CRUD apps, prefer API Resources for response shaping."
            )
        elif fillable_count >= 20 and hidden_count < 3:
            confidence = "medium"
            reasons.append(
                f"{fillable_count} fillable fields, only {hidden_count} hidden — "
                f"{exposed_count} fields exposed"
            )
            if not has_resource:
                suggestions.append(
                    f"Consider $hidden or a {model_info['class_name']}Resource "
                    f"for large list responses\n"
                    f"               Note: $hidden/$visible also hides fields from edit endpoints.\n"
                    f"               For CRUD apps, prefer API Resources for response shaping."
                )
            else:
                suggestions.append(
                    f"Verify {resource_path} limits fields for list vs detail views"
                )
        elif fillable_count >= threshold:
            confidence = "low"
            reasons.append(
                f"{fillable_count} fillable fields without select() optimization"
            )
            suggestions.append(
                "Use ->select(['field1', 'field2']) in list queries to limit columns"
            )

        if confidence is None:
            continue

        # -------------------------------------------------------------------
        # Controller analysis: direct returns + missing select()
        # -------------------------------------------------------------------
        direct_returns = []
        missing_selects = []

        # Only do file I/O for medium+ threshold findings to stay fast
        if confidence in ("high", "medium"):
            direct_returns = _check_controller_direct_returns(
                conn, model_info["class_name"], root
            )
            # Upgrade to high if direct returns found and we were medium
            if direct_returns and confidence == "medium":
                confidence = "high"
                reasons.append(
                    f"Model returned directly from controller "
                    f"({len(direct_returns)} location(s))"
                )

        if confidence == "low":
            missing_selects = _check_missing_select(
                conn, model_info["class_name"], root
            )
            if not missing_selects:
                # No bad query patterns — downgrade / skip low-confidence
                continue

        findings.append({
            "model_name": model_info["class_name"],
            "model_path": model_info["path"],
            "model_location": loc(model_info["path"], model_info["line_start"]),
            "fillable_count": fillable_count,
            "hidden_count": hidden_count,
            "exposed_count": exposed_count,
            "has_visible": has_visible,
            "has_resource": has_resource,
            "resource_path": resource_path,
            "confidence": confidence,
            "reasons": reasons,
            "suggestions": suggestions,
            "direct_returns": direct_returns[:5],   # Cap to avoid noise
            "missing_selects": missing_selects[:5],
        })

    # Sort: high → medium → low, then by exposed_count descending
    _conf_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(
        key=lambda f: (_conf_order.get(f["confidence"], 9), -f["exposed_count"])
    )

    return findings[:limit]


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("over-fetch")
@click.option(
    "--threshold", "-t",
    default=20,
    show_default=True,
    help="Minimum number of $fillable fields to flag a model",
)
@click.option(
    "--limit", "-n",
    default=30,
    show_default=True,
    help="Maximum number of findings to display",
)
@click.pass_context
def over_fetch_cmd(ctx, threshold, limit):
    """Detect models that serialize more fields than necessary in API responses.

    Finds large models ($fillable) without $hidden/$visible field filtering,
    controllers that return models directly without API Resources, and
    queries missing ->select() to limit fetched columns.

    Confidence levels:

    \b
      high   — 30+ fillable fields, no $hidden/$visible, no API Resource
      medium — 20+ fillable fields, minimal $hidden (< 3 fields)
      low    — 15+ fillable fields, missing ->select() in list queries

    \b
    Examples:
        roam over-fetch                  # Scan with default threshold (20 fields)
        roam over-fetch --threshold 30   # Only flag very large models
        roam over-fetch --limit 10       # Show top 10 findings only
        roam --json over-fetch           # JSON output
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    with open_db(readonly=True) as conn:
        findings = analyze_over_fetch(conn, threshold=threshold, limit=limit)

    # -------------------------------------------------------------------
    # Tally by confidence
    # -------------------------------------------------------------------
    by_confidence: dict[str, int] = defaultdict(int)
    for f in findings:
        by_confidence[f["confidence"]] += 1

    total = len(findings)
    high_count = by_confidence.get("high", 0)
    medium_count = by_confidence.get("medium", 0)
    low_count = by_confidence.get("low", 0)

    conf_parts = []
    if high_count:
        conf_parts.append(f"{high_count} high")
    if medium_count:
        conf_parts.append(f"{medium_count} medium")
    if low_count:
        conf_parts.append(f"{low_count} low")
    conf_str = ", ".join(conf_parts) if conf_parts else "none"

    if total:
        verdict = (
            f"{total} over-fetch pattern{'s' if total != 1 else ''} found "
            f"({conf_str})"
        )
    else:
        verdict = "No over-fetch patterns detected"

    # -------------------------------------------------------------------
    # JSON output
    # -------------------------------------------------------------------
    if json_mode:
        click.echo(to_json(json_envelope(
            "over-fetch",
            summary={
                "verdict": verdict,
                "total": total,
                "threshold": threshold,
                "by_confidence": dict(by_confidence),
            },
            findings=findings,
        )))
        return

    # -------------------------------------------------------------------
    # Text output
    # -------------------------------------------------------------------
    click.echo(f"VERDICT: {verdict}")

    if not findings:
        return

    click.echo()
    click.echo("Large models without field filtering:")

    for f in findings:
        conf = f["confidence"]
        model = f["model_name"]
        fillable = f["fillable_count"]
        hidden = f["hidden_count"]
        model_loc = f["model_location"]

        # Header line: [confidence]  ModelName (N fillable, M hidden)  path:line
        click.echo(
            f"  [{conf}]  {model} "
            f"({fillable} fillable, {hidden} hidden)  {model_loc}"
        )

        # Reason lines
        for reason in f["reasons"]:
            click.echo(f"          {reason}")

        # API Resource status
        if f["has_resource"]:
            click.echo(f"          Resource: {f['resource_path']}")

        # Direct return locations (for high-confidence)
        if f["direct_returns"]:
            click.echo(
                f"          Direct controller returns "
                f"({len(f['direct_returns'])} location(s)):"
            )
            for dr in f["direct_returns"][:3]:
                click.echo(f"            {dr['location']}  {dr['snippet']}")

        # Missing select locations (for low-confidence)
        if f["missing_selects"]:
            click.echo(
                f"          Queries without ->select() "
                f"({len(f['missing_selects'])} location(s)):"
            )
            for ms in f["missing_selects"][:3]:
                click.echo(f"            {ms['location']}  {ms['snippet']}")

        # Fix suggestion(s)
        for suggestion in f["suggestions"]:
            click.echo(f"          Fix: {suggestion}")

        click.echo()
