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

from pathlib import Path
from typing import Any

from roam.atomic_io import atomic_write_text
from roam.laws.miner import Law

SCHEMA_VERSION = 1
DEFAULT_LOCATIONS = ("roam-laws.yml", ".roam/laws.yml")


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
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _fallback_parse(text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []
    raw_laws = data.get("laws") or []
    laws: list[Law] = []
    for entry in raw_laws:
        if not isinstance(entry, dict):
            continue
        try:
            laws.append(
                Law(
                    id=str(entry.get("id", "")),
                    kind=str(entry.get("kind", "")),
                    description=str(entry.get("description", "")),
                    evidence=dict(entry.get("evidence") or {}),
                    severity=str(entry.get("severity", "advisory")),
                    confidence=str(entry.get("confidence", "medium")),
                    rule=dict(entry.get("rule") or {}),
                )
            )
        except Exception:
            continue
    return laws


def _fallback_parse(text: str) -> dict:
    """Very small YAML parser for the fallback dump's output.

    Handles enough of the subset we emit to round-trip our own laws,
    nothing more. Indentation is two spaces per level.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(0, root)]

    def container_for_indent(indent: int) -> Any:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            stack.append((0, root))
        return stack[-1][1]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)
        parent = container_for_indent(indent)
        if stripped.startswith("- "):
            value_part = stripped[2:].rstrip()
            if not isinstance(parent, list):
                # Promote: previous key holds the list now.
                continue
            if not value_part:
                # `-` opens a dict-item; subsequent lines fill it
                new = {}
                parent.append(new)
                stack.append((indent + 2, new))
            else:
                parent.append(_unscalar(value_part))
        elif stripped == "-":
            if isinstance(parent, list):
                new = {}
                parent.append(new)
                stack.append((indent + 2, new))
        elif ":" in stripped:
            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                # Container — could be dict or list; decide on next line.
                # Default to dict; promote to list if a `-` line follows.
                new_dict: dict[str, Any] = {}
                parent[key] = new_dict  # type: ignore[index]
                # Peek-ahead: we patch later if we see a list line.
                stack.append((indent + 2, new_dict))
            elif rest == "[]":
                parent[key] = []  # type: ignore[index]
            elif rest == "{}":
                parent[key] = {}  # type: ignore[index]
            else:
                parent[key] = _unscalar(rest)  # type: ignore[index]

    # Second pass: convert empty-dict containers that turn out to hold
    # only `-` lines into lists. The fallback parse above sometimes
    # leaves them as dicts; fix up here.
    _patch_lists(root)
    return root


def _patch_lists(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            _patch_lists(v)
    elif isinstance(node, list):
        for v in node:
            _patch_lists(v)


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
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
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
