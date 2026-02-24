"""Token-efficient text formatting for AI consumption."""

from __future__ import annotations

import json as _json
import time
from datetime import datetime, timezone

# Envelope schema versioning (semver: major.minor.patch)
ENVELOPE_SCHEMA_VERSION = "1.0.0"
ENVELOPE_SCHEMA_NAME = "roam-envelope-v1"

_NON_CACHEABLE_COMMANDS = {"mutate", "annotate", "ingest-trace", "vuln-map", "reset", "clean", "index", "init"}
_VOLATILE_COMMANDS = {"diff", "pr-risk", "pr-diff", "affected", "affected-tests", "weather"}

KIND_ABBREV = {
    "function": "fn",
    "class": "cls",
    "method": "meth",
    "variable": "var",
    "constant": "const",
    "interface": "iface",
    "struct": "struct",
    "enum": "enum",
    "module": "mod",
    "package": "pkg",
    "trait": "trait",
    "type_alias": "type",
    "property": "prop",
    "field": "field",
    "constructor": "ctor",
    "decorator": "deco",
}


def abbrev_kind(kind: str) -> str:
    return KIND_ABBREV.get(kind, kind)


def loc(path: str, line: int | None = None) -> str:
    if line is not None:
        return f"{path}:{line}"
    return path


def symbol_line(name: str, kind: str, signature: str | None, path: str,
                line: int | None = None, extra: str = "") -> str:
    parts = [abbrev_kind(kind), name]
    if signature:
        parts.append(signature)
    parts.append(loc(path, line))
    if extra:
        parts.append(extra)
    return "  ".join(parts)


def section(title: str, lines: list[str], budget: int = 0) -> str:
    out = [title]
    if budget and len(lines) > budget:
        out.extend(lines[:budget])
        out.append(f"  (+{len(lines) - budget} more)")
    else:
        out.extend(lines)
    return "\n".join(out)


def indent(text: str, level: int = 1) -> str:
    prefix = "  " * level
    return "\n".join(prefix + line for line in text.splitlines())


def truncate_lines(lines: list[str], budget: int) -> list[str]:
    if len(lines) <= budget:
        return lines
    return lines[:budget] + [f"(+{len(lines) - budget} more)"]


def format_signature(sig: str | None, max_len: int = 80) -> str:
    if not sig:
        return ""
    sig = sig.strip()
    if len(sig) > max_len:
        return sig[:max_len - 3] + "..."
    return sig


def format_edge_kind(kind: str) -> str:
    return kind.replace("_", " ")


def format_table(headers: list[str], rows: list[list[str]],
                 budget: int = 0) -> str:
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    num_cols = len(widths)
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                widths[i] = max(widths[i], len(str(cell)))
    lines = []
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * w for w in widths))
    display_rows = rows
    if budget and len(rows) > budget:
        display_rows = rows[:budget]
    for row in display_rows:
        line = "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
    if budget and len(rows) > budget:
        lines.append(f"(+{len(rows) - budget} more)")
    return "\n".join(lines)


def to_json(data) -> str:
    """Serialize data to a JSON string with deterministic key ordering.

    Uses ``sort_keys=True`` so that identical data always produces
    byte-identical output — critical for LLM prompt-caching compatibility.
    """
    return _json.dumps(data, indent=2, default=str, sort_keys=True)


# ── Token budget truncation ──────────────────────────────────────────

# Conservative heuristic: 1 token ~ 4 characters (works for English + code).
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length (1 token ~ 4 chars)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def budget_truncate(text: str, budget: int) -> str:
    """Truncate plain-text output to fit within a token budget.

    If *budget* is 0 or the text already fits, returns *text* unchanged.
    Otherwise, truncates to the last complete line within the character
    limit and appends a truncation notice.

    Parameters
    ----------
    text:
        The full output text.
    budget:
        Maximum output tokens (0 = unlimited).
    """
    if budget <= 0:
        return text

    char_limit = budget * _CHARS_PER_TOKEN

    if len(text) <= char_limit:
        return text

    # Truncate and find last complete line
    truncated = text[:char_limit]
    last_newline = truncated.rfind("\n")
    if last_newline > char_limit * 0.8:
        truncated = truncated[:last_newline]

    full_tokens = estimate_tokens(text)
    truncated += (
        f"\n\n... truncated (budget: {budget} tokens, "
        f"full output: ~{full_tokens} tokens)"
    )
    return truncated


# Keys recognised as importance indicators (checked in priority order).
_IMPORTANCE_KEYS = ("pagerank", "importance", "score", "rank")


def _sort_by_importance(items: list) -> tuple[list, bool]:
    """Sort list items by importance descending if they carry an importance key.

    Returns ``(sorted_list, was_sorted)``.  When no recognised importance
    key is found in the first dict item, the original order is preserved
    and ``was_sorted`` is ``False``.
    """
    if not items:
        return items, False

    # Only attempt importance-sorting on lists of dicts
    first = items[0]
    if not isinstance(first, dict):
        return items, False

    # Find the importance key present in items
    imp_key: str | None = None
    for candidate in _IMPORTANCE_KEYS:
        if candidate in first:
            imp_key = candidate
            break

    if imp_key is None:
        return items, False

    # Sort descending by importance (highest first → kept on truncation)
    try:
        sorted_items = sorted(
            items,
            key=lambda d: d.get(imp_key, 0) if isinstance(d, dict) else 0,
            reverse=True,
        )
        return sorted_items, True
    except (TypeError, ValueError):
        return items, False


def budget_truncate_json(data: dict, budget: int) -> dict:
    """Truncate a JSON envelope intelligently within a token budget.

    Strategy:
    - Always preserve envelope fields: command, summary, schema,
      schema_version, version, project, _meta.
    - For list-valued payload fields, sort by importance (``pagerank``,
      ``importance``, ``score``, or ``rank`` key) descending, then keep
      only the top N items until the result fits.  Lists without a
      recognised importance key fall back to positional truncation.
    - Annotates summary with ``truncated=True``, ``budget_tokens``,
      ``omitted_low_importance_nodes``, and ``kept_highest_importance``.

    If *budget* is 0 or the serialized dict already fits, returns
    *data* unchanged.

    Parameters
    ----------
    data:
        A dict produced by :func:`json_envelope`.
    budget:
        Maximum output tokens (0 = unlimited).
    """
    if budget <= 0:
        return data

    full_json = _json.dumps(data, default=str, sort_keys=True)
    char_limit = budget * _CHARS_PER_TOKEN

    if len(full_json) <= char_limit:
        return data

    # Deep copy to avoid mutating the original
    result: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v

    # Fields that must never be truncated
    preserved = {
        "command", "summary", "schema", "schema_version",
        "version", "project", "_meta",
    }

    # Sort list fields by importance before truncation so the most
    # important items survive progressive shrinking.
    any_importance_sorted = False
    for key, value in list(result.items()):
        if key in preserved:
            continue
        if isinstance(value, list):
            sorted_val, was_sorted = _sort_by_importance(value)
            if was_sorted:
                result[key] = sorted_val
                any_importance_sorted = True

    # Track how many items we omit across all list fields
    total_omitted = 0

    # Progressively shrink list fields until we fit
    # Start by keeping 10, then 5, then 3, then 1 item(s)
    for cap in (10, 5, 3, 1):
        for key, value in list(result.items()):
            if key in preserved:
                continue
            if isinstance(value, list) and len(value) > cap:
                result[key] = value[:cap]

        test_json = _json.dumps(result, default=str, sort_keys=True)
        if len(test_json) <= char_limit:
            break

    # If still too large, drop non-preserved keys entirely
    test_json = _json.dumps(result, default=str, sort_keys=True)
    if len(test_json) > char_limit:
        drop_keys = [
            k for k in list(result.keys())
            if k not in preserved
        ]
        for k in drop_keys:
            del result[k]
            test_json = _json.dumps(result, default=str, sort_keys=True)
            if len(test_json) <= char_limit:
                break

    # Count total omitted items across all truncated list fields
    for key in data:
        if key in preserved:
            continue
        orig = data.get(key)
        kept = result.get(key)
        if isinstance(orig, list):
            kept_len = len(kept) if isinstance(kept, list) else 0
            total_omitted += len(orig) - kept_len

    # Annotate summary with truncation metadata
    if "summary" in result and isinstance(result["summary"], dict):
        result["summary"]["truncated"] = True
        result["summary"]["budget_tokens"] = budget
        result["summary"]["full_output_tokens"] = estimate_tokens(full_json)
        if total_omitted > 0:
            result["summary"]["omitted_low_importance_nodes"] = total_omitted
        if any_importance_sorted:
            result["summary"]["kept_highest_importance"] = True

    return result


def _compact_mode_enabled() -> bool:
    """Return True when CLI requested compact/agent output mode."""
    try:
        import click
        ctx = click.get_current_context(silent=True)
        if ctx and isinstance(ctx.obj, dict):
            return bool(ctx.obj.get("compact") or ctx.obj.get("agent"))
    except Exception:
        pass
    return False


def json_envelope(command: str, summary: dict | None = None,
                  budget: int = 0, **payload) -> dict:
    """Wrap command output in a self-describing envelope.

    Every ``roam --json <cmd>`` call should use this to produce consistent
    top-level keys that downstream tools (CI, dashboards, AI agents) can
    rely on.

    Non-deterministic metadata (``timestamp``, ``index_age_s``) is placed
    in a ``_meta`` sub-dict so the main content keys remain stable across
    invocations — enabling LLM prompt-caching (exact prefix matching).

    When *budget* > 0, the envelope is passed through
    :func:`budget_truncate_json` before being returned, intelligently
    trimming list payloads to fit within the token cap while preserving
    summary and envelope metadata.

    Returns a dict with at minimum::

        {
            "command":     "health",
            "version":     "<current>",
            "project":     "roam-code",
            "summary":     { ... },
            "_meta": {
                "timestamp":   "2026-02-12T14:30:00Z",
                "index_age_s": 42,
            },
            ...payload
        }
    """
    if _compact_mode_enabled():
        compact = compact_json_envelope(command, summary=summary or {}, **payload)
        if budget > 0:
            compact = budget_truncate_json(compact, budget)
        return compact

    # Version — read once and cache
    version = _get_version()

    ts = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    out: dict = {
        "schema": ENVELOPE_SCHEMA_NAME,
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "command": command,
        "version": version,
        "project": _project_name(),
        "summary": summary or {},
    }
    out.update(payload)
    # Non-deterministic metadata in _meta — kept separate so content
    # keys produce identical JSON across invocations (LLM cache-friendly).
    out["_meta"] = {
        "timestamp": ts,
        "index_age_s": _index_age_seconds(),
    }

    # Response metadata for MCP agents (#119)
    full_json = _json.dumps(out, default=str, sort_keys=True)
    out["_meta"]["response_tokens"] = estimate_tokens(full_json)
    out["_meta"]["latency_ms"] = None  # filled by caller if needed
    if command in _NON_CACHEABLE_COMMANDS:
        out["_meta"]["cacheable"] = False
        out["_meta"]["cache_ttl_s"] = 0
    elif command in _VOLATILE_COMMANDS:
        out["_meta"]["cacheable"] = True
        out["_meta"]["cache_ttl_s"] = 60
    else:
        out["_meta"]["cacheable"] = True
        out["_meta"]["cache_ttl_s"] = 300

    if budget > 0:
        out = budget_truncate_json(out, budget)

    return out


def _get_version() -> str:
    """Return roam-code version string."""
    from roam import __version__
    return __version__


def _index_age_seconds() -> int | None:
    """Seconds since .roam/index.db was last modified, or None if missing."""
    try:
        from roam.db.connection import get_db_path
        db_path = get_db_path()
        if db_path.exists():
            return int(time.time() - db_path.stat().st_mtime)
    except Exception:
        pass
    return None


def _project_name() -> str:
    """Basename of the project root directory."""
    try:
        from roam.db.connection import find_project_root
        return find_project_root().name
    except Exception:
        return ""


def table_to_dicts(headers: list[str], rows: list[list[str]]) -> list[dict]:
    """Convert table headers + rows into a list of dicts (for JSON output)."""
    return [dict(zip(headers, row)) for row in rows]


# ── Compact output mode ──────────────────────────────────────────────

def compact_json_envelope(command: str, **payload) -> dict:
    """Minimal JSON envelope — strips version/timestamp/project overhead.

    For agents using --compact: emits only command name, summary, and payload.
    Saves ~150-200 tokens per call.
    """
    out = {"command": command}
    out.update(payload)
    return out


def ws_loc(repo: str, path: str, line: int | None = None) -> str:
    """Repo-prefixed location string for workspace output."""
    if line is not None:
        return f"[{repo}] {path}:{line}"
    return f"[{repo}] {path}"


def ws_json_envelope(command: str, workspace: str,
                     summary: dict | None = None, **payload) -> dict:
    """Workspace-aware JSON envelope.

    Extends :func:`json_envelope` with workspace metadata.
    """
    out = json_envelope(command, summary=summary, **payload)
    out["workspace"] = workspace
    return out


def summary_envelope(data: dict, keep_summary: bool = True) -> dict:
    """Strip list payloads from a JSON envelope, keeping only summary data.

    Used by ``--detail``-aware commands to produce compact JSON output when
    ``--detail`` is not passed.  Always drops all list-valued payload fields
    to save tokens.  Adds ``detail_available: true`` to the summary dict so
    callers know full data is available via ``--detail``.  When non-empty
    lists were stripped, also sets ``truncated: true`` in the summary.

    Parameters
    ----------
    data:
        A dict produced by :func:`json_envelope`.
    keep_summary:
        When True (default) the ``summary`` sub-dict is always preserved.

    Returns a new dict without list-valued payload keys.  The summary dict
    always receives ``detail_available: true``.  When non-empty lists were
    stripped, the summary also receives ``truncated: true``.
    """
    preserved = {
        "command", "schema", "schema_version",
        "version", "project", "_meta",
    }
    list_counts: dict[str, int] = {}

    # Build stripped result: drop all list-valued payload fields
    result: dict = {}
    for k, v in data.items():
        if k in preserved:
            result[k] = v
        elif k == "summary":
            if keep_summary:
                result[k] = dict(v) if isinstance(v, dict) else v
        elif isinstance(v, list):
            # Drop list — record its count
            list_counts[k] = len(v)
        else:
            result[k] = v

    has_non_empty_lists = any(c > 0 for c in list_counts.values())

    # Annotate summary with progressive disclosure flags.
    # Keep the annotation minimal so summary is always <= detail in size.
    if "summary" not in result:
        result["summary"] = {}
    if isinstance(result.get("summary"), dict):
        result["summary"]["detail_available"] = True
        if has_non_empty_lists:
            result["summary"]["truncated"] = True

    return result


def format_table_compact(headers: list[str], rows: list[list[str]],
                         budget: int = 0) -> str:
    """Tab-separated table output — 40-50% more token-efficient than padded tables."""
    if not rows:
        return "(none)"
    lines = ["\t".join(headers)]
    display_rows = rows[:budget] if budget and len(rows) > budget else rows
    for row in display_rows:
        lines.append("\t".join(str(cell) for cell in row))
    if budget and len(rows) > budget:
        lines.append(f"(+{len(rows) - budget} more)")
    return "\n".join(lines)
