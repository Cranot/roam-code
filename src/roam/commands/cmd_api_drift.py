"""Detect mismatches between backend API responses and frontend type definitions."""

from __future__ import annotations

import re
from pathlib import Path

import click

from roam.db.connection import open_db, find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Common auto-added fields to skip when comparing (not meaningful drift)
# ---------------------------------------------------------------------------

_SKIP_FIELDS = frozenset({
    "id", "createdAt", "updatedAt", "deletedAt",
    "created_at", "updated_at", "deleted_at",
})


# ---------------------------------------------------------------------------
# snake_case → camelCase conversion (mirrors Laravel's API auto-convert)
# ---------------------------------------------------------------------------

def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase.

    Examples:
        usage_period_id → usagePeriodId
        a_logariasmos   → aLogariasmos
        energo          → energo
    """
    parts = name.split("_")
    if not parts:
        return name
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_to_snake(name: str) -> str:
    """Convert camelCase/PascalCase to snake_case.

    Examples:
        usagePeriodId → usage_period_id
        myDataUid     → my_data_uid
    """
    # Insert underscore before uppercase letters that follow lowercase or digits
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    # Handle sequences like "myDATA" → "my_DATA" → "my_data"
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    return s.lower()


def _normalize_field(name: str) -> str:
    """Normalize a field name to camelCase for canonical comparison."""
    if "_" in name:
        return _snake_to_camel(name)
    return name


# ---------------------------------------------------------------------------
# PHP model parsing
# ---------------------------------------------------------------------------

_FILLABLE_RE = re.compile(
    r'\$fillable\s*=\s*\[([^\]]*)\]',
    re.DOTALL,
)
_HIDDEN_RE = re.compile(
    r'\$hidden\s*=\s*\[([^\]]*)\]',
    re.DOTALL,
)
_APPENDS_RE = re.compile(
    r'\$appends\s*=\s*\[([^\]]*)\]',
    re.DOTALL,
)
_STRING_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")


def _extract_php_array_strings(array_body: str) -> list[str]:
    """Extract string literals from a PHP array body."""
    return [m.group(1) or m.group(2) for m in _STRING_RE.finditer(array_body)]


def _parse_php_model(source: str) -> dict | None:
    """Parse a PHP model file and extract $fillable, $hidden, $appends fields.

    Returns a dict with keys 'fillable', 'hidden', 'appends', or None if
    no $fillable is found (meaning it's not a model file we care about).
    """
    fillable_match = _FILLABLE_RE.search(source)
    if not fillable_match:
        return None

    fillable = _extract_php_array_strings(fillable_match.group(1))

    hidden = []
    hidden_match = _HIDDEN_RE.search(source)
    if hidden_match:
        hidden = _extract_php_array_strings(hidden_match.group(1))

    appends = []
    appends_match = _APPENDS_RE.search(source)
    if appends_match:
        appends = _extract_php_array_strings(appends_match.group(1))

    return {
        "fillable": fillable,
        "hidden": hidden,
        "appends": appends,
    }


def _infer_model_name(file_path: str) -> str:
    """Get the model class name from a PHP file path.

    E.g. 'app/Models/Kinisi.php' → 'Kinisi'
    """
    return Path(file_path).stem


# ---------------------------------------------------------------------------
# TypeScript interface/type parsing
# ---------------------------------------------------------------------------

# Matches TypeScript interfaces and type aliases.
# Two patterns are needed because their syntax differs:
#   interface FooBar { ... }            (no '=' before the brace)
#   type FooBar = { ... }              (requires '=' before the brace)
# A single regex with [={] fails because 'interface X {' does not have '='.
_TS_INTERFACE_RE = re.compile(
    r'(?:export\s+)?interface\s+(\w+)\s*(?:extends[^{]*)?\s*\{([^}]*)\}'
    r'|(?:export\s+)?type\s+(\w+)\s*=\s*\{([^}]*)\}',
    re.DOTALL,
)
# Matches: fieldName?: type; or fieldName: type;  (optional and required)
_TS_FIELD_RE = re.compile(
    r'^\s*(?:readonly\s+)?(\w+)\??:\s*[^;/\n]+[;,]?\s*(?://.*)?$',
    re.MULTILINE,
)


def _parse_ts_interfaces(source: str) -> dict[str, list[str]]:
    """Parse TypeScript source and extract all interface/type definitions.

    Returns a dict mapping interface_name → [field_names].

    The regex has two alternatives:
      groups 1,2  →  interface declaration
      groups 3,4  →  type alias declaration
    """
    result: dict[str, list[str]] = {}
    for m in _TS_INTERFACE_RE.finditer(source):
        # Pick whichever alternative matched (interface vs type)
        iface_name = m.group(1) or m.group(3)
        body = m.group(2) or m.group(4)
        if not iface_name or not body:
            continue
        fields = [
            fm.group(1)
            for fm in _TS_FIELD_RE.finditer(body)
            if fm.group(1) and not fm.group(1).startswith("//")
            and fm.group(1) not in ("readonly", "export", "import")
        ]
        if fields:
            result[iface_name] = fields
    return result


def _infer_ts_model_name(file_path: str) -> str:
    """Get a normalized base name from a TypeScript file path.

    E.g. 'src/types/models/kinisi.ts' → 'Kinisi'
         'src/types/api/KinisiResponse.ts' → 'KinisiResponse'
    """
    stem = Path(file_path).stem
    # PascalCase it if it starts lowercase (common for model type files)
    if stem and stem[0].islower():
        return stem[0].upper() + stem[1:]
    return stem


# ---------------------------------------------------------------------------
# Model ↔ Interface matching
# ---------------------------------------------------------------------------

def _name_similarity(a: str, b: str) -> float:
    """Compute a simple name similarity score between two strings.

    Returns 0.0 (no match) to 1.0 (exact match).  Uses multiple heuristics:
    - Exact match (1.0)
    - One contains the other (0.9)
    - One is a prefix of the other after stripping common suffixes (0.8)
    """
    a_lower = a.lower()
    b_lower = b.lower()

    if a_lower == b_lower:
        return 1.0

    # Strip common suffixes for matching (KinisiResponse → Kinisi)
    _SUFFIXES = ("response", "data", "dto", "model", "entity", "resource",
                 "request", "payload", "form", "item", "record")

    def strip_suffixes(name: str) -> str:
        n = name.lower()
        for sfx in _SUFFIXES:
            if n.endswith(sfx):
                return n[: -len(sfx)]
        return n

    a_base = strip_suffixes(a)
    b_base = strip_suffixes(b)

    if a_base == b_base:
        return 0.9
    if a_base and (a_base in b_lower or b_lower.startswith(a_base)):
        return 0.8
    if b_base and (b_base in a_lower or a_lower.startswith(b_base)):
        return 0.8

    return 0.0


def _match_models_to_interfaces(
    backend_models: dict[str, dict],
    frontend_interfaces: dict[str, dict],
    min_similarity: float = 0.8,
) -> list[dict]:
    """Match backend models to frontend interfaces by name similarity.

    Returns a list of match dicts:
        {
            'php_model': str,
            'php_file': str,
            'ts_interface': str,
            'ts_file': str,
            'similarity': float,
        }
    """
    matches = []
    used_ts = set()

    for php_name, php_info in backend_models.items():
        best_sim = 0.0
        best_ts_name = None

        for ts_name in frontend_interfaces:
            sim = _name_similarity(php_name, ts_name)
            if sim > best_sim:
                best_sim = sim
                best_ts_name = ts_name

        if best_ts_name and best_sim >= min_similarity and best_ts_name not in used_ts:
            used_ts.add(best_ts_name)
            matches.append({
                "php_model": php_name,
                "php_file": php_info["file"],
                "ts_interface": best_ts_name,
                "ts_file": frontend_interfaces[best_ts_name]["file"],
                "similarity": best_sim,
            })

    return matches


# ---------------------------------------------------------------------------
# Field comparison
# ---------------------------------------------------------------------------

def _compare_fields(
    php_name: str,
    php_info: dict,
    ts_name: str,
    ts_info: dict,
) -> list[dict]:
    """Compare PHP model fields vs TS interface fields and produce findings.

    Returns a list of finding dicts:
        {
            'confidence': 'high'|'medium'|'low',
            'kind': 'missing_in_frontend'|'missing_in_backend'|'name_mismatch',
            'message': str,
            'php_field': str | None,
            'ts_field': str | None,
            'php_file': str,
            'ts_file': str,
        }
    """
    findings = []

    # Build the set of fields the backend sends in API responses:
    # $fillable fields (minus $hidden) + $appends (computed properties)
    hidden_set = set(php_info["hidden"])
    backend_raw = [
        f for f in php_info["fillable"]
        if f not in hidden_set
    ] + php_info["appends"]

    # Normalize backend fields to camelCase for comparison
    backend_camel: dict[str, str] = {}  # camelCase → original snake_case
    for raw in backend_raw:
        camel = _normalize_field(raw)
        backend_camel[camel] = raw

    # Collect frontend field names (already camelCase in TS)
    frontend_fields: set[str] = set(ts_info["fields"])

    # Filter out common auto-added fields from both sides
    backend_comparable = {
        k: v for k, v in backend_camel.items()
        if k not in _SKIP_FIELDS
    }
    frontend_comparable = {
        f for f in frontend_fields
        if f not in _SKIP_FIELDS
    }

    backend_set = set(backend_comparable.keys())

    # Fields in frontend but NOT in backend (high confidence — will be undefined)
    for ts_field in sorted(frontend_comparable - backend_set):
        # Check for fuzzy name match (potential naming mismatch)
        fuzzy_match = None
        ts_snake = _camel_to_snake(ts_field)
        for be_field in backend_comparable:
            be_snake = _camel_to_snake(be_field)
            sim = _name_similarity(ts_snake, be_snake)
            if 0.5 <= sim < 1.0:
                fuzzy_match = be_field
                break

        if fuzzy_match:
            findings.append({
                "confidence": "low",
                "kind": "name_mismatch",
                "message": (
                    f"Frontend has '{ts_field}' but backend has similar '{fuzzy_match}' "
                    f"— possible naming mismatch"
                ),
                "php_field": backend_comparable.get(fuzzy_match),
                "ts_field": ts_field,
                "php_file": php_info["file"],
                "ts_file": ts_info["file"],
            })
        else:
            findings.append({
                "confidence": "high",
                "kind": "missing_in_backend",
                "message": (
                    f"Frontend expects '{ts_field}' but backend has no such field "
                    f"(will be undefined at runtime)"
                ),
                "php_field": None,
                "ts_field": ts_field,
                "php_file": php_info["file"],
                "ts_file": ts_info["file"],
            })

    # Fields in backend but NOT in frontend (medium confidence — over-sending)
    for be_camel in sorted(backend_set - frontend_comparable):
        findings.append({
            "confidence": "medium",
            "kind": "missing_in_frontend",
            "message": (
                f"Backend sends '{be_camel}' (raw: '{backend_comparable[be_camel]}') "
                f"but frontend interface doesn't define it (wasted bandwidth)"
            ),
            "php_field": backend_comparable[be_camel],
            "ts_field": None,
            "php_file": php_info["file"],
            "ts_file": ts_info["file"],
        })

    return findings


# ---------------------------------------------------------------------------
# File collection helpers
# ---------------------------------------------------------------------------

def _is_php_model_path(path: str) -> bool:
    """Return True if the path looks like a PHP model file."""
    normalized = path.replace("\\", "/")
    # Must be PHP and inside a Models directory
    if not normalized.endswith(".php"):
        return False
    # Accept App/Models, app/Models, Models/ patterns
    return (
        "/Models/" in normalized
        or "/models/" in normalized
        or normalized.endswith("Model.php")
    )


def _is_ts_type_path(path: str) -> bool:
    """Return True if the path looks like a TypeScript type/model definition file."""
    normalized = path.replace("\\", "/")
    if not (normalized.endswith(".ts") or normalized.endswith(".tsx")):
        return False
    # Must be in a types or models directory, not a test file
    return (
        "/types/" in normalized
        or "/models/" in normalized
        or "/interfaces/" in normalized
        or "/api/" in normalized
    ) and "test" not in normalized.lower() and "spec" not in normalized.lower()


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@click.command("api-drift")
@click.option("--limit", "-n", default=50, help="Max findings to show")
@click.option(
    "--confidence",
    type=click.Choice(["high", "medium", "low", "all"], case_sensitive=False),
    default="all",
    help="Filter findings by confidence level",
)
@click.option(
    "--model",
    default=None,
    help="Filter to a specific model name (e.g. Kinisi)",
)
@click.pass_context
def api_drift_cmd(ctx, limit, confidence, model):
    """Detect mismatches between backend API responses and frontend type definitions.

    Compares PHP model $fillable/$appends fields against TypeScript interface
    definitions to find:

    \b
    [high]   Frontend expects a field the backend doesn't send → undefined at runtime
    [medium] Backend sends a field the frontend doesn't type  → wasted bandwidth
    [low]    Possible naming mismatch (fuzzy name match)

    The backend auto-converts snake_case to camelCase in API responses, so
    usage_period_id becomes usagePeriodId before comparison.

    Hidden fields ($hidden) are excluded from backend comparison since they
    are intentionally omitted from API responses.

    NOTE: Cross-repo drift detection (separate PHP + TS repos) is planned for
    a future 'roam ws api-drift' workspace command. This command operates on
    a single indexed project containing both PHP and TypeScript files.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    project_root = find_project_root()

    with open_db(readonly=True) as conn:
        # ----------------------------------------------------------------
        # Step 1: Collect all indexed file paths by language/path pattern
        # ----------------------------------------------------------------
        all_files = conn.execute(
            "SELECT path, language FROM files"
        ).fetchall()

        php_model_paths: list[str] = []
        ts_type_paths: list[str] = []

        for row in all_files:
            path = row["path"]
            lang = (row["language"] or "").lower()

            if lang == "php" and _is_php_model_path(path):
                php_model_paths.append(path)
            elif lang in ("typescript", "tsx", "ts") and _is_ts_type_path(path):
                ts_type_paths.append(path)
            else:
                # Fallback: infer from extension/path when language tag is missing
                if _is_php_model_path(path):
                    php_model_paths.append(path)
                elif _is_ts_type_path(path):
                    ts_type_paths.append(path)

        # ----------------------------------------------------------------
        # Step 2: Parse backend model files
        # ----------------------------------------------------------------
        # backend_models: model_name → {file, fillable, hidden, appends}
        backend_models: dict[str, dict] = {}

        for rel_path in php_model_paths:
            full_path = project_root / rel_path
            try:
                source = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            parsed = _parse_php_model(source)
            if parsed is None:
                continue

            model_name = _infer_model_name(rel_path)

            # If --model filter is active, skip non-matching models
            if model and model_name.lower() != model.lower():
                continue

            # Deduplicate: if same model appears twice (trait/abstract), prefer
            # the one with more fillable fields
            if model_name in backend_models:
                existing = backend_models[model_name]
                if len(parsed["fillable"]) <= len(existing["fillable"]):
                    continue

            backend_models[model_name] = {
                "file": rel_path,
                "fillable": parsed["fillable"],
                "hidden": parsed["hidden"],
                "appends": parsed["appends"],
            }

        # ----------------------------------------------------------------
        # Step 3: Parse frontend TypeScript type files
        # ----------------------------------------------------------------
        # frontend_interfaces: interface_name → {file, fields}
        frontend_interfaces: dict[str, dict] = {}

        for rel_path in ts_type_paths:
            full_path = project_root / rel_path
            try:
                source = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            ifaces = _parse_ts_interfaces(source)
            for iface_name, fields in ifaces.items():
                # If --model filter is active, only include matching interfaces
                if model:
                    sim = _name_similarity(model, iface_name)
                    if sim < 0.5:
                        continue

                if iface_name not in frontend_interfaces:
                    frontend_interfaces[iface_name] = {
                        "file": rel_path,
                        "fields": fields,
                    }
                else:
                    # Prefer the interface with more fields
                    if len(fields) > len(frontend_interfaces[iface_name]["fields"]):
                        frontend_interfaces[iface_name] = {
                            "file": rel_path,
                            "fields": fields,
                        }

        # ----------------------------------------------------------------
        # Graceful no-op when one side is empty
        # ----------------------------------------------------------------
        has_backend = bool(backend_models)
        has_frontend = bool(frontend_interfaces)

        if not has_backend and not has_frontend:
            msg = (
                "No backend/frontend pair found. Ensure the project contains "
                "PHP model files (in app/Models/) and TypeScript type files "
                "(in src/types/ or similar)."
            )
            if json_mode:
                click.echo(to_json(json_envelope(
                    "api-drift",
                    summary={"error": msg, "findings": 0},
                    matches=[],
                    findings=[],
                )))
            else:
                click.echo(f"api-drift: {msg}")
            return

        if not has_backend:
            msg = (
                "No PHP model files found (looking for app/Models/*.php with $fillable). "
                "Cross-repo drift detection is planned for 'roam ws api-drift'."
            )
            if json_mode:
                click.echo(to_json(json_envelope(
                    "api-drift",
                    summary={"error": msg, "findings": 0},
                    matches=[],
                    findings=[],
                )))
            else:
                click.echo(f"api-drift: {msg}")
            return

        if not has_frontend:
            msg = (
                "No TypeScript type files found (looking for *.ts in types/models/interfaces/ "
                "directories). Cross-repo drift detection is planned for 'roam ws api-drift'."
            )
            if json_mode:
                click.echo(to_json(json_envelope(
                    "api-drift",
                    summary={"error": msg, "findings": 0},
                    matches=[],
                    findings=[],
                )))
            else:
                click.echo(f"api-drift: {msg}")
            return

        # ----------------------------------------------------------------
        # Step 4: Match models to interfaces
        # ----------------------------------------------------------------
        matches = _match_models_to_interfaces(backend_models, frontend_interfaces)

        matched_php_names = {m["php_model"] for m in matches}
        matched_ts_names = {m["ts_interface"] for m in matches}

        unmatched_backend = sorted(
            n for n in backend_models if n not in matched_php_names
        )
        unmatched_frontend = sorted(
            n for n in frontend_interfaces if n not in matched_ts_names
        )

        # ----------------------------------------------------------------
        # Step 5: Compare fields for each matched pair
        # ----------------------------------------------------------------
        # findings_by_pair: [(match_dict, [finding_dict, ...])]
        findings_by_pair: list[tuple[dict, list[dict]]] = []
        all_findings: list[dict] = []

        for match in matches:
            php_info = backend_models[match["php_model"]]
            ts_info = frontend_interfaces[match["ts_interface"]]

            pair_findings = _compare_fields(
                match["php_model"], php_info,
                match["ts_interface"], ts_info,
            )

            # Apply confidence filter
            if confidence != "all":
                pair_findings = [
                    f for f in pair_findings
                    if f["confidence"] == confidence
                ]

            findings_by_pair.append((match, pair_findings))
            all_findings.extend(pair_findings)

        # Count by confidence
        n_high = sum(1 for f in all_findings if f["confidence"] == "high")
        n_medium = sum(1 for f in all_findings if f["confidence"] == "medium")
        n_low = sum(1 for f in all_findings if f["confidence"] == "low")
        n_total = len(all_findings)

        # ----------------------------------------------------------------
        # JSON output
        # ----------------------------------------------------------------
        if json_mode:
            json_matches = []
            for match, pair_findings in findings_by_pair:
                json_matches.append({
                    "php_model": match["php_model"],
                    "php_file": match["php_file"],
                    "ts_interface": match["ts_interface"],
                    "ts_file": match["ts_file"],
                    "similarity": round(match["similarity"], 2),
                    "findings": pair_findings,
                })

            click.echo(to_json(json_envelope(
                "api-drift",
                summary={
                    "findings": n_total,
                    "high": n_high,
                    "medium": n_medium,
                    "low": n_low,
                    "models_matched": len(matches),
                    "models_total_backend": len(backend_models),
                    "interfaces_total_frontend": len(frontend_interfaces),
                },
                matches=json_matches,
                unmatched={
                    "backend_only": unmatched_backend,
                    "frontend_only": unmatched_frontend,
                },
            )))
            return

        # ----------------------------------------------------------------
        # Text output
        # ----------------------------------------------------------------
        verdict_parts = []
        if n_high:
            verdict_parts.append(f"{n_high} high")
        if n_medium:
            verdict_parts.append(f"{n_medium} medium")
        if n_low:
            verdict_parts.append(f"{n_low} low")
        verdict_suffix = f" ({', '.join(verdict_parts)})" if verdict_parts else ""

        click.echo(
            f"VERDICT: {n_total} API drift issue{'s' if n_total != 1 else ''} found"
            f"{verdict_suffix}"
        )
        click.echo(
            f"Models matched: {len(matches)} of {len(backend_models)}"
        )

        if not all_findings:
            click.echo("\nNo drift detected in matched model/interface pairs.")
        else:
            click.echo()

        # Confidence label formatting
        _CONF_LABEL = {
            "high":   "[high]  ",
            "medium": "[medium]",
            "low":    "[low]   ",
        }

        shown = 0
        for match, pair_findings in findings_by_pair:
            if not pair_findings:
                continue

            click.echo(
                f"{match['php_model']} (PHP) <-> {match['ts_interface']} (TS):"
            )

            for finding in pair_findings:
                if shown >= limit:
                    remaining = n_total - shown
                    click.echo(f"\n  (+{remaining} more findings — use -n to increase limit)")
                    break

                label = _CONF_LABEL.get(finding["confidence"], "[?]     ")
                click.echo(f"  {label}  {finding['message']}")

                if finding["ts_field"] and finding["ts_file"]:
                    click.echo(f"           TS: {finding['ts_file']}")
                if finding["php_field"] and finding["php_file"]:
                    click.echo(f"           PHP: {finding['php_file']} (in $fillable)")
                if finding.get("kind") == "missing_in_backend" and not finding["php_field"]:
                    click.echo(f"           PHP: {finding['php_file']} (not in $fillable or $appends)")

                shown += 1

            click.echo()

            if shown >= limit:
                break

        # Unmatched models summary
        if unmatched_backend or unmatched_frontend:
            click.echo("Unmatched models:")
            if unmatched_backend:
                names = ", ".join(unmatched_backend[:20])
                more = f" (+{len(unmatched_backend) - 20} more)" if len(unmatched_backend) > 20 else ""
                click.echo(f"  Backend only:  {names}{more}")
            if unmatched_frontend:
                names = ", ".join(unmatched_frontend[:20])
                more = f" (+{len(unmatched_frontend) - 20} more)" if len(unmatched_frontend) > 20 else ""
                click.echo(f"  Frontend only: {names}{more}")
