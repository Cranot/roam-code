"""Mermaid diagram generation helpers.

Provides small building blocks for producing Mermaid flowcharts from
roam's architecture data (layers, clusters, tour).  Every function
returns plain strings -- the caller is responsible for assembling them
with ``click.echo()``.
"""

from __future__ import annotations

import re


def sanitize_id(name: str) -> str:
    """Convert a file path or symbol name to a valid Mermaid node ID.

    Replaces characters that are invalid in Mermaid identifiers
    (``/``, ``.``, ``-``, ``:``, `` ``) with underscores, and strips
    leading digits so the ID is always a valid identifier.
    """
    s = re.sub(r'[^A-Za-z0-9_]', '_', name)
    # Mermaid IDs must not start with a digit
    if s and s[0].isdigit():
        s = '_' + s
    return s


def node(node_id: str, label: str) -> str:
    """Generate a Mermaid node definition with a quoted label."""
    safe_id = sanitize_id(node_id)
    safe_label = label.replace('"', "'")
    return f'    {safe_id}["{safe_label}"]'


def edge(source: str, target: str) -> str:
    """Generate a Mermaid edge (``source --> target``)."""
    return f'    {sanitize_id(source)} --> {sanitize_id(target)}'


def subgraph(name: str, node_lines: list[str]) -> str:
    """Generate a Mermaid subgraph block.

    *node_lines* should be pre-formatted lines (from :func:`node`).
    """
    safe_name = name.replace('"', "'")
    lines = [f'    subgraph "{safe_name}"']
    for n in node_lines:
        # Indent an extra level inside the subgraph
        lines.append(f'    {n}')
    lines.append('    end')
    return '\n'.join(lines)


def diagram(direction: str, elements: list[str]) -> str:
    """Assemble a complete Mermaid diagram.

    *direction* is ``TD`` (top-down), ``LR`` (left-right), etc.
    *elements* is a list of pre-formatted lines or blocks.
    """
    lines = [f'graph {direction}']
    lines.extend(elements)
    return '\n'.join(lines)
