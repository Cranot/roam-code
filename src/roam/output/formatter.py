"""Token-efficient text formatting for AI consumption."""

import json as _json
import os
import time
from datetime import datetime, timezone

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
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
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
    """Serialize data to a JSON string."""
    return _json.dumps(data, indent=2, default=str)


def json_envelope(command: str, summary: dict | None = None, **payload) -> dict:
    """Wrap command output in a self-describing envelope.

    Every ``roam --json <cmd>`` call should use this to produce consistent
    top-level keys that downstream tools (CI, dashboards, AI agents) can
    rely on.

    Returns a dict with at minimum::

        {
            "command":     "health",
            "version":     "5.0.0",
            "timestamp":   "2026-02-12T14:30:00Z",
            "index_age_s": 42,
            "project":     "roam-code",
            "summary":     { ... },
            ...payload
        }
    """
    # Version — read once and cache
    version = _get_version()

    ts = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    out: dict = {
        "command": command,
        "version": version,
        "timestamp": ts,
        "index_age_s": _index_age_seconds(),
        "project": _project_name(),
        "summary": summary or {},
    }
    out.update(payload)
    return out


def _get_version() -> str:
    """Return roam-code version string."""
    try:
        from importlib.metadata import version
        return version("roam-code")
    except Exception:
        return "dev"


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
