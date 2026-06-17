"""Detect mismatches between backend API responses and frontend type definitions.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because api-drift outputs are invocation-scoped API mismatch
detections — not per-location violations. See action.yml
_SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B propagation plan
+ W1148 audit memo.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# W1005-followup-H -- Pattern 3a (cross-command metric divergence) sealing.
#
# Pre-W1005-followup-H, ``roam api-drift --confidence`` accepted ONLY the
# 3-tier ``{high, medium, low, all}`` emit vocab (where ``all`` was the
# bypass sentinel that skipped filtering entirely). An agent fluent in
# the W547 canonical vocab (``critical / error / high / warning /
# medium / low / info / note``) who typed ``--confidence critical``
# (because that's what ``roam smells``, ``roam alerts``, ``roam
# api-changes``, ``roam dogfood-aggregate``, ``roam pr-bundle add risk``,
# etc. accept post-W1005 / -C / -D / -F / -G) hit a click usage error 2.
#
# Path A-variant fix. Widen Click.Choice to accept canonical W547 tokens
# AND the ``all`` bypass sentinel. Project canonical tokens onto the
# emit-vocab (``high``/``medium``/``low``) BEFORE the equality filter.
# EMIT vocab unchanged: every ``finding["confidence"]`` value is still
# one of ``high``/``medium``/``low`` so downstream consumers (the
# ``_CONF_LABEL`` formatter, JSON summary buckets) keep working.
#
# Projection mirrors :func:`roam.output._severity.severity_to_confidence_level`
# (the W565 closed table; see ``_DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL``):
#
# * ``critical`` / ``error`` / ``high`` -> ``high`` -- CI-gate tier maps
#   onto the missing_in_backend (runtime undefined) class.
# * ``warning`` / ``medium`` -> ``medium`` -- mid-tier maps onto the
#   missing_in_frontend (wasted bandwidth) class.
# * ``info`` / ``low`` / ``note`` -> ``low`` -- floor maps onto the
#   fuzzy-name-mismatch (heuristic) class.
#
# The ``all`` sentinel STAYS as a bypass token that short-circuits the
# filter entirely (lines 675-676 below) so the existing semantics
# ``--confidence all`` -> "return every finding" are preserved
# byte-for-byte.
#
# Asymmetry note for the next maintainer: this is the LAST narrow Choice
# site across the W1005 Pattern 3a cluster. Sibling references:
#   * cmd_api_changes._CANONICAL_TO_SEMVER (W1005-followup-F)
#   * cmd_dogfood_aggregate._CANONICAL_TO_SHORTCODE (W1005-followup-G)
#   * cmd_pr_bundle._CANONICAL_TO_RISK_SHORTCODE (W1005-followup-G)
# Each command picks the projection map shape that matches its own EMIT
# vocab (SemVer 3-tier, H/M/L short-codes, low/medium/high here). The
# uniform discipline: widen INPUT, project once, never widen EMIT.
# ---------------------------------------------------------------------------

# The bypass sentinel that short-circuits the confidence filter. Kept as
# a module constant so tests can pin the literal and a future maintainer
# can grep one symbol instead of chasing the string.
_CONFIDENCE_BYPASS_SENTINEL = "all"

# Canonical W547 token -> emit-vocab confidence projection. Closed map;
# keys mirror :data:`roam.output._severity.SEVERITY_LEVELS` plus the
# CVSS-style aliases tracked in
# :data:`roam.output._severity.SEVERITY_ALIASES`. Values are the
# 3-tier api-drift emit vocab (high/medium/low) -- NOT the canonical
# 4-tier (critical/error/warning/info) -- because that's what the
# detector emits at the finding level and what the equality filter
# (line 675-676) compares against.
_CANONICAL_TO_CONFIDENCE: dict[str, str] = {
    # W547 canonical 4-tier
    "critical": "high",
    "error": "high",
    "warning": "medium",
    "info": "low",
    # CVSS-style aliases (round-trip with OSV / npm-audit / trivy feeds)
    "high": "high",
    "medium": "medium",
    "low": "low",
    "note": "low",
}


def _project_confidence_input(label: str) -> str:
    """Project a user-supplied confidence/severity token to the emit vocab.

    Case-insensitive. Unknown labels fall through unchanged so the
    Click.Choice (which is the closed-enum gate) stays the
    source-of-truth for what's accepted; this helper is purely the
    projection layer.

    Examples
    --------
    >>> _project_confidence_input("critical")
    'high'
    >>> _project_confidence_input("WARNING")
    'medium'
    >>> _project_confidence_input("low")
    'low'
    """
    return _CANONICAL_TO_CONFIDENCE.get(label.lower(), label.lower())


# ---------------------------------------------------------------------------
# Common auto-added fields to skip when comparing (not meaningful drift)
# ---------------------------------------------------------------------------

_SKIP_FIELDS = frozenset(
    {
        "id",
        "createdAt",
        "updatedAt",
        "deletedAt",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)


# ---------------------------------------------------------------------------
# snake_case → camelCase conversion (mirrors Laravel's API auto-convert)
# ---------------------------------------------------------------------------


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase.

    Examples:
        created_at → createdAt
        first_name → firstName
        status     → status
    """
    parts = name.split("_")
    if not parts:
        return name
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camel_to_snake(name: str) -> str:
    """Convert camelCase/PascalCase to snake_case.

    Examples:
        firstName     → first_name
        userAPIToken  → user_api_token
    """
    # Insert underscore before uppercase letters that follow lowercase or digits
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Handle sequences like "userAPI" → "user_API" → "user_api"
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
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
    r"\$fillable\s*=\s*\[([^\]]*)\]",
    re.DOTALL,
)
_HIDDEN_RE = re.compile(
    r"\$hidden\s*=\s*\[([^\]]*)\]",
    re.DOTALL,
)
_APPENDS_RE = re.compile(
    r"\$appends\s*=\s*\[([^\]]*)\]",
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

    E.g. 'app/Models/Order.php' → 'Order'
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
    r"(?:export\s+)?interface\s+(\w+)\s*(?:extends[^{]*)?\s*\{([^}]*)\}"
    r"|(?:export\s+)?type\s+(\w+)\s*=\s*\{([^}]*)\}",
    re.DOTALL,
)
# Matches: fieldName?: type; or fieldName: type;  (optional and required)
_TS_FIELD_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)\??:\s*[^;/\n]+[;,]?\s*(?://.*)?$",
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
            if fm.group(1) and not fm.group(1).startswith("//") and fm.group(1) not in ("readonly", "export", "import")
        ]
        if fields:
            result[iface_name] = fields
    return result


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

    # Strip common suffixes for matching (OrderResponse → Order)
    _SUFFIXES = (
        "response",
        "data",
        "dto",
        "model",
        "entity",
        "resource",
        "request",
        "payload",
        "form",
        "item",
        "record",
    )

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
            matches.append(
                {
                    "php_model": php_name,
                    "php_file": php_info["file"],
                    "ts_interface": best_ts_name,
                    "ts_file": frontend_interfaces[best_ts_name]["file"],
                    "similarity": best_sim,
                }
            )

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
    backend_raw = [f for f in php_info["fillable"] if f not in hidden_set] + php_info["appends"]

    # Normalize backend fields to camelCase for comparison
    backend_camel: dict[str, str] = {}  # camelCase → original snake_case
    for raw in backend_raw:
        camel = _normalize_field(raw)
        backend_camel[camel] = raw

    # Collect frontend field names (already camelCase in TS)
    frontend_fields: set[str] = set(ts_info["fields"])

    # Filter out common auto-added fields from both sides
    backend_comparable = {k: v for k, v in backend_camel.items() if k not in _SKIP_FIELDS}
    frontend_comparable = {f for f in frontend_fields if f not in _SKIP_FIELDS}

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
            findings.append(
                {
                    "confidence": "low",
                    "kind": "name_mismatch",
                    "message": (
                        f"Frontend has '{ts_field}' but backend has similar '{fuzzy_match}' — possible naming mismatch"
                    ),
                    "php_field": backend_comparable.get(fuzzy_match),
                    "ts_field": ts_field,
                    "php_file": php_info["file"],
                    "ts_file": ts_info["file"],
                }
            )
        else:
            findings.append(
                {
                    "confidence": "high",
                    "kind": "missing_in_backend",
                    "message": (
                        f"Frontend expects '{ts_field}' but backend has no such field (will be undefined at runtime)"
                    ),
                    "php_field": None,
                    "ts_field": ts_field,
                    "php_file": php_info["file"],
                    "ts_file": ts_info["file"],
                }
            )

    # Fields in backend but NOT in frontend (medium confidence — over-sending)
    for be_camel in sorted(backend_set - frontend_comparable):
        findings.append(
            {
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
            }
        )

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
    return "/Models/" in normalized or "/models/" in normalized or normalized.endswith("Model.php")


def _is_ts_type_path(path: str) -> bool:
    """Return True if the path looks like a TypeScript type/model definition file."""
    normalized = path.replace("\\", "/")
    if not (normalized.endswith(".ts") or normalized.endswith(".tsx")):
        return False
    # Must be in a types or models directory, not a test file
    return (
        ("/types/" in normalized or "/models/" in normalized or "/interfaces/" in normalized or "/api/" in normalized)
        and "test" not in normalized.lower()
        and "spec" not in normalized.lower()
    )


def _emit_skipped_api_drift(
    *,
    json_mode: bool,
    msg: str,
    state: str,
    backend_models: dict[str, dict] | None = None,
    frontend_interfaces: dict[str, dict] | None = None,
) -> None:
    """Emit the canonical skipped/no-op envelope for missing API sides."""
    verdict = f"api-drift skipped — {msg}"
    backend_names = sorted(backend_models or {})
    frontend_names = sorted(frontend_interfaces or {})
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "api-drift",
                    summary={
                        "verdict": verdict,
                        "state": state,
                        "error": msg,
                        "findings": 0,
                        "models_total_backend": len(backend_names),
                        "interfaces_total_frontend": len(frontend_names),
                        "partial_success": False,
                    },
                    matches=[],
                    findings=[],
                    unmatched={
                        "backend_only": backend_names,
                        "frontend_only": frontend_names,
                    },
                )
            )
        )
    else:
        click.echo(f"VERDICT: {verdict}")


def _append_inferred_contract_path(php_model_paths: list[str], ts_type_paths: list[str], path: str) -> None:
    if _is_php_model_path(path):
        php_model_paths.append(path)
    elif _is_ts_type_path(path):
        ts_type_paths.append(path)


def _collect_contract_paths(rows) -> tuple[list[str], list[str]]:
    php_model_paths: list[str] = []
    ts_type_paths: list[str] = []

    for row in rows:
        path = row["path"]
        lang = (row["language"] or "").lower()
        if lang == "php" and _is_php_model_path(path):
            php_model_paths.append(path)
        elif lang in ("typescript", "tsx", "ts") and _is_ts_type_path(path):
            ts_type_paths.append(path)
        else:
            _append_inferred_contract_path(php_model_paths, ts_type_paths, path)

    return php_model_paths, ts_type_paths


def _read_source(project_root: Path, rel_path: str) -> str | None:
    try:
        return (project_root / rel_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _backend_model_matches_filter(model_name: str, model_filter: str | None) -> bool:
    return not model_filter or model_name.lower() == model_filter.lower()


def _prefer_backend_model(existing: dict | None, parsed: dict) -> bool:
    return existing is None or len(parsed["fillable"]) > len(existing["fillable"])


def _backend_model_record(rel_path: str, parsed: dict) -> dict:
    return {
        "file": rel_path,
        "fillable": parsed["fillable"],
        "hidden": parsed["hidden"],
        "appends": parsed["appends"],
    }


def _collect_backend_models(
    project_root: Path, php_model_paths: list[str], model_filter: str | None
) -> dict[str, dict]:
    backend_models: dict[str, dict] = {}

    for rel_path in php_model_paths:
        source = _read_source(project_root, rel_path)
        if source is None:
            continue

        parsed = _parse_php_model(source)
        if parsed is None:
            continue

        model_name = _infer_model_name(rel_path)
        if not _backend_model_matches_filter(model_name, model_filter):
            continue
        if not _prefer_backend_model(backend_models.get(model_name), parsed):
            continue
        backend_models[model_name] = _backend_model_record(rel_path, parsed)

    return backend_models


def _frontend_interface_matches_filter(model_filter: str | None, iface_name: str) -> bool:
    return not model_filter or _name_similarity(model_filter, iface_name) >= 0.5


def _prefer_frontend_interface(existing: dict | None, fields: set[str]) -> bool:
    return existing is None or len(fields) > len(existing["fields"])


def _maybe_store_frontend_interface(
    frontend_interfaces: dict[str, dict],
    rel_path: str,
    iface_name: str,
    fields: set[str],
    model_filter: str | None,
) -> None:
    if not _frontend_interface_matches_filter(model_filter, iface_name):
        return
    if _prefer_frontend_interface(frontend_interfaces.get(iface_name), fields):
        frontend_interfaces[iface_name] = {"file": rel_path, "fields": fields}


def _store_frontend_interfaces_from_source(
    frontend_interfaces: dict[str, dict],
    rel_path: str,
    source: str,
    model_filter: str | None,
) -> None:
    for iface_name, fields in _parse_ts_interfaces(source).items():
        _maybe_store_frontend_interface(frontend_interfaces, rel_path, iface_name, fields, model_filter)


def _collect_frontend_interfaces(
    project_root: Path, ts_type_paths: list[str], model_filter: str | None
) -> dict[str, dict]:
    frontend_interfaces: dict[str, dict] = {}

    for rel_path in ts_type_paths:
        source = _read_source(project_root, rel_path)
        if source is None:
            continue
        _store_frontend_interfaces_from_source(frontend_interfaces, rel_path, source, model_filter)

    return frontend_interfaces


def _emit_missing_api_side(
    json_mode: bool, backend_models: dict[str, dict], frontend_interfaces: dict[str, dict]
) -> bool:
    has_backend = bool(backend_models)
    has_frontend = bool(frontend_interfaces)

    if not has_backend and not has_frontend:
        _emit_skipped_api_drift(
            json_mode=json_mode,
            msg=(
                "No backend/frontend pair found. Ensure the project contains "
                "PHP model files (in app/Models/) and TypeScript type files "
                "(in src/types/ or similar)."
            ),
            state="no_backend_frontend_pair",
        )
        return True

    if not has_backend:
        _emit_skipped_api_drift(
            json_mode=json_mode,
            msg=(
                "No PHP model files found (looking for app/Models/*.php with $fillable). "
                "Cross-repo drift detection is planned for 'roam ws api-drift'."
            ),
            state="no_backend_models",
            frontend_interfaces=frontend_interfaces,
        )
        return True

    if not has_frontend:
        _emit_skipped_api_drift(
            json_mode=json_mode,
            msg=(
                "No TypeScript type files found (looking for *.ts in types/models/interfaces/ "
                "directories). Cross-repo drift detection is planned for 'roam ws api-drift'."
            ),
            state="no_frontend_interfaces",
            backend_models=backend_models,
        )
        return True

    return False


def _filter_pair_findings(pair_findings: list[dict], confidence: str) -> list[dict]:
    if confidence.lower() == _CONFIDENCE_BYPASS_SENTINEL:
        return pair_findings
    projected = _project_confidence_input(confidence)
    return [finding for finding in pair_findings if finding["confidence"] == projected]


def _collect_findings_by_pair(
    matches: list[dict],
    backend_models: dict[str, dict],
    frontend_interfaces: dict[str, dict],
    confidence: str,
) -> tuple[list[tuple[dict, list[dict]]], list[dict]]:
    findings_by_pair: list[tuple[dict, list[dict]]] = []
    all_findings: list[dict] = []

    for match in matches:
        pair_findings = _compare_fields(
            match["php_model"],
            backend_models[match["php_model"]],
            match["ts_interface"],
            frontend_interfaces[match["ts_interface"]],
        )
        pair_findings = _filter_pair_findings(pair_findings, confidence)
        findings_by_pair.append((match, pair_findings))
        all_findings.extend(pair_findings)

    return findings_by_pair, all_findings


def _confidence_counts(findings: list[dict]) -> tuple[int, int, int]:
    high = sum(1 for finding in findings if finding["confidence"] == "high")
    medium = sum(1 for finding in findings if finding["confidence"] == "medium")
    low = sum(1 for finding in findings if finding["confidence"] == "low")
    return high, medium, low


def _unmatched_names(
    matches: list[dict], backend_models: dict[str, dict], frontend_interfaces: dict[str, dict]
) -> tuple[list[str], list[str]]:
    matched_php_names = {match["php_model"] for match in matches}
    matched_ts_names = {match["ts_interface"] for match in matches}
    unmatched_backend = sorted(name for name in backend_models if name not in matched_php_names)
    unmatched_frontend = sorted(name for name in frontend_interfaces if name not in matched_ts_names)
    return unmatched_backend, unmatched_frontend


def _json_match(match: dict, pair_findings: list[dict]) -> dict:
    return {
        "php_model": match["php_model"],
        "php_file": match["php_file"],
        "ts_interface": match["ts_interface"],
        "ts_file": match["ts_file"],
        "similarity": round(match["similarity"], 2),
        "findings": pair_findings,
    }


def _api_drift_summary(
    n_total: int,
    n_high: int,
    n_medium: int,
    n_low: int,
    matches: list[dict],
    backend_models: dict[str, dict],
    frontend_interfaces: dict[str, dict],
) -> dict:
    return {
        "verdict": f"{n_total} API drift findings ({n_high} high, {n_medium} medium, {n_low} low) across {len(matches)} matched models",
        "state": "ok",
        "findings": n_total,
        "high": n_high,
        "medium": n_medium,
        "low": n_low,
        "models_matched": len(matches),
        "models_total_backend": len(backend_models),
        "interfaces_total_frontend": len(frontend_interfaces),
    }


def _emit_api_drift_json(
    findings_by_pair: list[tuple[dict, list[dict]]],
    unmatched_backend: list[str],
    unmatched_frontend: list[str],
    summary: dict,
) -> None:
    click.echo(
        to_json(
            json_envelope(
                "api-drift",
                summary=summary,
                matches=[_json_match(match, pair_findings) for match, pair_findings in findings_by_pair],
                unmatched={
                    "backend_only": unmatched_backend,
                    "frontend_only": unmatched_frontend,
                },
            )
        )
    )


def _text_verdict_suffix(n_high: int, n_medium: int, n_low: int) -> str:
    parts = []
    if n_high:
        parts.append(f"{n_high} high")
    if n_medium:
        parts.append(f"{n_medium} medium")
    if n_low:
        parts.append(f"{n_low} low")
    return f" ({', '.join(parts)})" if parts else ""


_CONF_LABEL = {
    "high": "[high]  ",
    "medium": "[medium]",
    "low": "[low]   ",
}


def _emit_text_header(
    n_total: int, n_high: int, n_medium: int, n_low: int, matches: list[dict], backend_models: dict[str, dict]
) -> None:
    verdict_suffix = _text_verdict_suffix(n_high, n_medium, n_low)
    plural = "s" if n_total != 1 else ""
    click.echo(f"VERDICT: {n_total} API drift issue{plural} found{verdict_suffix}")
    click.echo(f"Models matched: {len(matches)} of {len(backend_models)}")


def _emit_finding_locations(finding: dict) -> None:
    if finding["ts_field"] and finding["ts_file"]:
        click.echo(f"           TS: {finding['ts_file']}")
    if finding["php_field"] and finding["php_file"]:
        click.echo(f"           PHP: {finding['php_file']} (in $fillable)")
    if finding.get("kind") == "missing_in_backend" and not finding["php_field"]:
        click.echo(f"           PHP: {finding['php_file']} (not in $fillable or $appends)")


def _emit_pair_findings(
    match: dict, pair_findings: list[dict], shown: int, limit: int, n_total: int
) -> tuple[int, bool]:
    click.echo(f"{match['php_model']} (PHP) <-> {match['ts_interface']} (TS):")

    for finding in pair_findings:
        if shown >= limit:
            click.echo(f"\n  (+{n_total - shown} more findings — use -n to increase limit)")
            return shown, True

        label = _CONF_LABEL.get(finding["confidence"], "[?]     ")
        click.echo(f"  {label}  {finding['message']}")
        _emit_finding_locations(finding)
        shown += 1

    click.echo()
    return shown, shown >= limit


def _emit_all_findings(findings_by_pair: list[tuple[dict, list[dict]]], n_total: int, limit: int) -> None:
    shown = 0
    for match, pair_findings in findings_by_pair:
        if not pair_findings:
            continue
        shown, limit_reached = _emit_pair_findings(match, pair_findings, shown, limit, n_total)
        if limit_reached:
            break


def _limited_names(names: list[str]) -> str:
    listed = ", ".join(names[:20])
    more = f" (+{len(names) - 20} more)" if len(names) > 20 else ""
    return f"{listed}{more}"


def _emit_unmatched_models(unmatched_backend: list[str], unmatched_frontend: list[str]) -> None:
    if not unmatched_backend and not unmatched_frontend:
        return

    click.echo("Unmatched models:")
    if unmatched_backend:
        click.echo(f"  Backend only:  {_limited_names(unmatched_backend)}")
    if unmatched_frontend:
        click.echo(f"  Frontend only: {_limited_names(unmatched_frontend)}")


def _emit_api_drift_text(
    *,
    findings_by_pair: list[tuple[dict, list[dict]]],
    all_findings: list[dict],
    limit: int,
    matches: list[dict],
    backend_models: dict[str, dict],
    unmatched_backend: list[str],
    unmatched_frontend: list[str],
    n_high: int,
    n_medium: int,
    n_low: int,
) -> None:
    n_total = len(all_findings)
    _emit_text_header(n_total, n_high, n_medium, n_low, matches, backend_models)

    if not all_findings:
        click.echo("\nNo drift detected in matched model/interface pairs.")
    else:
        click.echo()
        _emit_all_findings(findings_by_pair, n_total, limit)

    _emit_unmatched_models(unmatched_backend, unmatched_frontend)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@roam_capability(
    name="api-drift",
    category="reports",
    summary="Detect mismatches between backend API responses and frontend type definitions",
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
@click.command("api-drift")
@click.option("--limit", "-n", default=50, help="Max findings to show")
@click.option(
    "--confidence",
    # Emit vocab (high/medium/low) + W547 canonical 4-tier (critical/error/
    # warning/info) + CVSS aliases (note) + bypass sentinel (all). Pattern
    # 3a alias widening per W1005-followup-H -- canonical-aware agents pass
    # any of them without hitting click usage error 2. EMIT stays
    # high/medium/low; ``all`` short-circuits the filter (see line ~675).
    type=click.Choice(
        [
            "high",
            "medium",
            "low",  # emit vocab (back-compat)
            "critical",
            "error",
            "warning",
            "info",
            "note",  # W547 canonical aliases
            _CONFIDENCE_BYPASS_SENTINEL,  # bypass sentinel
        ],
        case_sensitive=False,
    ),
    default=_CONFIDENCE_BYPASS_SENTINEL,
    help=(
        "Filter findings by confidence level. Accepts the api-drift emit "
        "vocab {high, medium, low} OR W547 canonical tokens {critical, "
        "error, warning, info, note} -- canonical tokens project onto the "
        "emit vocab (critical/error -> high; warning -> medium; info/note "
        "-> low). Pass 'all' (the default) to bypass the filter."
    ),
)
@click.option(
    "--model",
    default=None,
    help="Filter to a specific model name (e.g. Order)",
)
@click.pass_context
def api_drift_cmd(ctx, limit, confidence, model):
    """Detect mismatches between backend API responses and frontend type definitions.

    Compares PHP model $fillable/$appends fields against TypeScript interface
    definitions to find:

    Unlike ``orphan-routes`` (which finds dead endpoints) and ``over-fetch``
    (which detects models exposing too many fields), this command focuses
    on field-level type contract divergence between backend and frontend.

    \b
    [high]   Frontend expects a field the backend doesn't send → undefined at runtime
    [medium] Backend sends a field the frontend doesn't type  → wasted bandwidth
    [low]    Possible naming mismatch (fuzzy name match)

    The backend auto-converts snake_case to camelCase in API responses, so
    created_at becomes createdAt before comparison.

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
        all_files = conn.execute("SELECT path, language FROM files").fetchall()
        php_model_paths, ts_type_paths = _collect_contract_paths(all_files)
        backend_models = _collect_backend_models(project_root, php_model_paths, model)
        frontend_interfaces = _collect_frontend_interfaces(project_root, ts_type_paths, model)

        if _emit_missing_api_side(json_mode, backend_models, frontend_interfaces):
            return

        matches = _match_models_to_interfaces(backend_models, frontend_interfaces)
        unmatched_backend, unmatched_frontend = _unmatched_names(matches, backend_models, frontend_interfaces)
        findings_by_pair, all_findings = _collect_findings_by_pair(
            matches,
            backend_models,
            frontend_interfaces,
            confidence,
        )
        n_high, n_medium, n_low = _confidence_counts(all_findings)
        n_total = len(all_findings)

        if json_mode:
            summary = _api_drift_summary(
                n_total,
                n_high,
                n_medium,
                n_low,
                matches,
                backend_models,
                frontend_interfaces,
            )
            _emit_api_drift_json(findings_by_pair, unmatched_backend, unmatched_frontend, summary)
            return

        _emit_api_drift_text(
            findings_by_pair=findings_by_pair,
            all_findings=all_findings,
            limit=limit,
            matches=matches,
            backend_models=backend_models,
            unmatched_backend=unmatched_backend,
            unmatched_frontend=unmatched_frontend,
            n_high=n_high,
            n_medium=n_medium,
            n_low=n_low,
        )
