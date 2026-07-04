"""Serialize / deserialize laws to and from ``roam-laws.yml``.

The on-disk schema is intentionally tiny — agents that hand-edit the
file should be able to read it in seconds. Top-level shape::

    version: 1
    generated_by: roam laws mine
    laws:
      - id: snake_case_functions
        kind: naming
        description: Functions must be snake_case
        severity: advisory
        confidence: high
        evidence:
          sample_size: 1450
          conformance_pct: 93
          ...
        rule:
          kind: naming
          symbol_kind: function
          style: snake_case

Uses PyYAML when available, otherwise falls back to a small inline
writer / reader that handles this schema only. The fallback is enough
for round-tripping our own output, which is what the unit tests
verify.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from roam.atomic_io import atomic_write_text
from roam.laws.miner import Law

SCHEMA_VERSION = 1
DEFAULT_LOCATIONS = ("roam-laws.yml", ".roam/laws.yml")
_DIGITS_RE = r"\d(?:_?\d)*"
_INT_LITERAL_RE = re.compile(rf"^[+-]?{_DIGITS_RE}$")
_FLOAT_LITERAL_RE = re.compile(
    rf"^[+-]?(?:"
    rf"(?:{_DIGITS_RE}\.(?:{_DIGITS_RE})?|\.(?:{_DIGITS_RE}))(?:[eE][+-]?{_DIGITS_RE})?"
    rf"|{_DIGITS_RE}[eE][+-]?{_DIGITS_RE}"
    rf"|inf(?:inity)?|nan"
    rf")$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------


def dump_laws_yaml(laws: list[Law]) -> str:
    """Serialize *laws* to a YAML string.

    Prefers PyYAML for readability; falls back to a hand-rolled writer
    so the surface works even when the optional ``[mcp]`` extras are
    not installed.
    """
    doc = {
        "version": SCHEMA_VERSION,
        "generated_by": "roam laws mine",
        "laws": [law.to_dict() for law in laws],
    }
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    except ImportError:
        return _fallback_dump(doc)


def _fallback_dump(doc: dict[str, Any], indent: int = 0) -> str:
    """Minimal YAML writer that handles dicts / lists / primitives.

    Covers the surface required by ``roam laws mine`` output. Not
    general-purpose — strings with special characters get quoted.
    """
    lines: list[str] = []
    pad = "  " * indent
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_fallback_dump(v, indent + 1))
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: []")
            elif isinstance(v, dict):
                lines.append(f"{pad}{k}: {{}}")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(doc, list):
        for item in doc:
            if isinstance(item, dict):
                lines.append(f"{pad}-")
                lines.append(_fallback_dump(item, indent + 1))
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(doc)}")
    return "\n".join(filter(None, lines))


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # Quote any string that contains YAML-significant characters or
    # could be misread as another scalar type.
    if any(c in s for c in ":#&*!,[]{}\"'\n") or s in ("null", "true", "false"):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_laws_yaml(text: str) -> list[Law]:
    """Parse *text* (YAML) into a list of :class:`Law`.

    Tolerates the fallback dump's slightly limited output as well as
    the PyYAML output.
    """
    data = _parse_laws_document_without_requiring_pyyaml(text)

    if not isinstance(data, dict):
        return []
    raw_laws = data.get("laws") or []
    laws: list[Law] = []
    for entry in raw_laws:
        law = _law_from_entry(entry)
        if law is not None:
            laws.append(law)
    return laws


def _parse_laws_document_without_requiring_pyyaml(text: str) -> Any:
    """Load YAML while keeping PyYAML optional and parser bugs visible."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return _fallback_parse(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _law_from_entry(raw: Any) -> Law | None:
    """Normalize one raw ``laws`` entry into a :class:`Law`.

    Returns ``None`` when the entry is not a dict or cannot be
    constructed as a ``Law`` (missing fields become their default
    string values rather than failing).
    """
    if not isinstance(raw, dict):
        return None
    try:
        return Law(
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            description=str(raw.get("description", "")),
            evidence=dict(raw.get("evidence") or {}),
            severity=str(raw.get("severity", "advisory")),
            confidence=str(raw.get("confidence", "medium")),
            rule=dict(raw.get("rule") or {}),
        )
    except (TypeError, ValueError):
        return None


def _fallback_parse(text: str) -> dict:
    """Very small YAML parser for the fallback dump's output.

    Handles enough of the subset we emit to round-trip our own laws,
    nothing more. Indentation is two spaces per level.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(0, root)]

    def container_for_indent(indent: int) -> Any:
        while stack and stack[-1][0] > indent:
            stack.pop()
        if not stack:
            stack.append((0, root))
        return stack[-1][1]

    raw_lines = text.splitlines()
    for line_index, raw_line in enumerate(raw_lines):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)
        parent = container_for_indent(indent)
        children_are_list = _fallback_child_container_should_be_list(raw_lines, line_index, indent)
        _preserve_fallback_yaml_line(parent, stack, indent, stripped, children_are_list)
    return root


def _fallback_child_container_should_be_list(raw_lines: list[str], line_index: int, indent: int) -> bool:
    for raw_line in raw_lines[line_index + 1 :]:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.lstrip()
        child_indent = len(raw_line) - len(stripped)
        return child_indent > indent and stripped.startswith("-")
    return False


def _open_fallback_list_item_scope(parent: Any, stack: list[tuple[int, Any]], indent: int) -> None:
    if not isinstance(parent, list):
        return
    new: dict[str, Any] = {}
    parent.append(new)
    stack.append((indent + 2, new))


def _preserve_fallback_list_line(parent: Any, stack: list[tuple[int, Any]], indent: int, stripped: str) -> bool:
    if stripped == "-":
        _open_fallback_list_item_scope(parent, stack, indent)
        return True
    if not stripped.startswith("- "):
        return False
    if not isinstance(parent, list):
        # Promote: previous key holds the list now.
        return True
    value_part = stripped[2:].rstrip()
    if not value_part:
        # `-` opens a dict-item; subsequent lines fill it.
        _open_fallback_list_item_scope(parent, stack, indent)
        return True
    parent.append(_unscalar(value_part))
    return True


def _preserve_fallback_mapping_line(
    parent: Any,
    stack: list[tuple[int, Any]],
    indent: int,
    stripped: str,
    children_are_list: bool,
) -> None:
    if ":" not in stripped:
        return
    key, _, rest = stripped.partition(":")
    key = key.strip()
    rest = rest.strip()
    if rest == "":
        # Container — choose list only for the list syntax emitted by _fallback_dump.
        new_container: dict[str, Any] | list[Any] = [] if children_are_list else {}
        parent[key] = new_container  # type: ignore[index]
        stack.append((indent + 2, new_container))
    elif rest == "[]":
        parent[key] = []  # type: ignore[index]
    elif rest == "{}":
        parent[key] = {}  # type: ignore[index]
    else:
        parent[key] = _unscalar(rest)  # type: ignore[index]


def _preserve_fallback_yaml_line(
    parent: Any,
    stack: list[tuple[int, Any]],
    indent: int,
    stripped: str,
    children_are_list: bool,
) -> None:
    if _preserve_fallback_list_line(parent, stack, indent, stripped):
        return
    _preserve_fallback_mapping_line(parent, stack, indent, stripped, children_are_list)


def _unscalar(s: str) -> Any:
    s = s.strip()
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if _INT_LITERAL_RE.match(s):
        return int(s)
    if _FLOAT_LITERAL_RE.match(s):
        return float(s)
    return s


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def find_laws_file(repo_root: Path, explicit: str | None = None) -> Path | None:
    """Locate the laws file for the repo, preferring explicit arguments.

    Search order:
      1. *explicit* (if provided)
      2. ``<repo_root>/roam-laws.yml``
      3. ``<repo_root>/.roam/laws.yml``
    """
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for rel in DEFAULT_LOCATIONS:
        candidate = repo_root / rel
        if candidate.exists():
            return candidate
    return None


def write_laws_file(path: Path, laws: list[Law]) -> None:
    """Write *laws* to *path* as YAML, creating parent dirs as needed.

    Atomic write: a torn ``roam-laws.yml`` would round-trip back to ``[]``
    in :func:`load_laws_yaml` (the parse error path), silently erasing the
    user's mined-laws state on next read. The temp-file + ``os.replace``
    pattern keeps the previous file intact on crash.
    """
    atomic_write_text(path, dump_laws_yaml(laws))
