"""Build the website's bounded, source-derived Python import atlas.

Not a Roam call graph: this intentionally smaller lens resolves literal Python
imports to local modules and rolls them up by directory. No index or network.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from collections import Counter
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "templates/distribution/landing-page/atlas-data.json"
GROUPS = {
    "core": (
        "Core & interfaces",
        "Bring the tools together",
        "The entry points and shared modules connect Roam's analysis to its consumers.",
        460,
        280,
    ),
    "commands": (
        "Commands",
        "Turn a question into a check",
        "The command modules gather evidence and return results an agent can act on.",
        250,
        170,
    ),
    "index": (
        "Indexing",
        "Build the repository picture",
        "Discover files, extract symbols, and connect references before an agent asks its next question.",
        490,
        110,
    ),
    "languages": (
        "Languages",
        "Read the code's structure",
        "Language extractors translate syntax into symbols and references. Analysis depth varies by language.",
        700,
        115,
    ),
    "graph": (
        "Graph analysis",
        "Follow the connections",
        "Graph algorithms explore the indexed structure: clusters, layers, cycles, and paths.",
        700,
        290,
    ),
    "db": (
        "Local database",
        "Keep the groundwork reusable",
        "SQLite stores the repository index so later checks can reuse the extracted structure.",
        520,
        440,
    ),
    "output": (
        "Output & contracts",
        "Make the answer usable",
        "Formatters and schemas shape results for people and agents, including their limits.",
        270,
        405,
    ),
    "mcp_extras": (
        "Agent connection",
        "Meet agents where they work",
        "MCP helpers support sessions, progress, completions, and other client-facing behavior.",
        80,
        280,
    ),
    "security": (
        "Security",
        "Inspect sensitive boundaries",
        "Security modules support checks and redaction. Static evidence is not a security certification.",
        105,
        95,
    ),
    "bridges": (
        "Cross-language bridges",
        "Connect across boundaries",
        "Bridges add known framework and cross-language connections that syntax alone can miss.",
        750,
        465,
    ),
    "search": (
        "Search",
        "Find a useful starting point",
        "Local search helpers find candidate symbols and context for the next investigation.",
        870,
        200,
    ),
    "runtime": (
        "Runtime evidence",
        "Bring observations into view",
        "Runtime modules ingest supported traces and help compare observed behavior with static structure.",
        890,
        385,
    ),
    "evidence": (
        "Evidence",
        "Keep the qualifications attached",
        "Evidence modules package observations and receipts. Integrity is not proof of completeness or approval.",
        80,
        450,
    ),
    "support": (
        "Supporting systems",
        "Put the pieces to work",
        "Other Roam packages support workflows, policies, refactoring, and specialized analysis.",
        335,
        540,
    ),
}


def module_name(path: Path, source: Path) -> str:
    parts = path.relative_to(source).with_suffix("").parts
    return ".".join(("roam", *parts[:-1])) if parts[-1] == "__init__" else ".".join(("roam", *parts))


def build(source: Path) -> dict:
    paths = sorted(source.rglob("*.py"))
    if not paths:
        raise ValueError("No Python sources scanned")
    modules = {module_name(path, source): path for path in paths}
    if len(modules) != len(paths):
        raise ValueError("Ambiguous Python module identities")
    digest = hashlib.sha256()
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {key: [] for key in GROUPS}
    group_by_module = {}
    trees = {}
    for name, path in modules.items():
        rel = path.relative_to(source)
        group = "core" if len(rel.parts) == 1 else rel.parts[0]
        if group not in GROUPS:
            group = "support"
        group_by_module[name] = group
        counts[group] += 1
        examples[group].append("src/roam/" + rel.as_posix())
        raw = path.read_bytes()
        # Git checkouts may use CRLF on Windows and LF on Linux. Hash a
        # documented canonical representation, retaining every other byte.
        digest.update(rel.as_posix().encode() + b"\0" + raw.replace(b"\r\n", b"\n") + b"\0")
        trees[name] = ast.parse(raw, filename=str(path))  # fail closed on unreadable/unparsed source

    edges = set()
    unresolved = set()
    for name, tree in trees.items():
        package = name if modules[name].name == "__init__.py" else name.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parents = package.split(".")
                    if node.level > len(parents):
                        unresolved.add((name, node.lineno, "relative import outside package"))
                        continue
                    base = ".".join(parents[: len(parents) - node.level + 1] + ([base] if base else []))
                # Resolve imported submodules first; imported symbols attach to
                # the known source module, never an invented symbol edge.
                candidates = [
                    f"{base}.{alias.name}" if f"{base}.{alias.name}" in modules else base for alias in node.names
                ]
            for target in candidates:
                if target in modules and target != name:
                    edges.add((name, target))
                elif target.startswith("roam.") and target not in modules:
                    unresolved.add((name, node.lineno, target))
    rolled = Counter(
        (group_by_module[a], group_by_module[b]) for a, b in edges if group_by_module[a] != group_by_module[b]
    )
    return {
        "schema_version": 1,
        "source": "Roam working-tree snapshot · Python sources only",
        "source_sha256": digest.hexdigest(),
        "source_hash_definition": "SHA-256 of sorted relative POSIX paths and Python source bytes with CRLF normalized to LF, NUL-delimited",
        "files_scanned": len(paths),
        "module_connections": len(edges),
        "unresolved_local_imports": len(unresolved),
        "definition": "Unique directed source-module → imported-module pairs from Python AST imports; grouped by directory. Includes conditional and type-checking imports. Not a call graph or runtime trace.",
        "limitations": "Dynamic imports, non-Python code and symbol-level calls are outside this view. A missing connection is not proof of independence. Supporting systems combines the remaining packages.",
        "nodes": [
            {
                "id": key,
                "label": values[0],
                "title": values[1],
                "description": values[2],
                "x": values[3],
                "y": values[4],
                "files": counts[key],
                "examples": examples[key][:3],
            }
            for key, values in GROUPS.items()
            if counts[key]
        ],
        "edges": [{"source": a, "target": b, "count": count} for (a, b), count in sorted(rolled.items())],
    }


def render_svg(data: dict) -> str:
    # Standalone SVG images cannot inherit page CSS. Mirror the shared light
    # palette here; source contracts compare these paints with the site tokens.
    nodes = {node["id"]: node for node in data["nodes"]}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 620" role="img" aria-labelledby="title desc">',
        '<title id="title">Inside Roam: Python import connections</title>',
        '<desc id="desc">Source-derived import map grouped by area. Larger circles contain more Python files. Not a runtime call graph.</desc>',
        '<defs><pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M 32 0 H 0 V 32" fill="none" stroke="#0049b7" stroke-opacity=".035"/></pattern></defs>',
        '<rect width="960" height="620" fill="#fffefa"/>',
        '<rect width="960" height="620" fill="url(#grid)"/>',
    ]
    for edge in data["edges"]:
        a, b = nodes[edge["source"]], nodes[edge["target"]]
        parts.append(
            f'<path d="M {a["x"]} {a["y"]} L {b["x"]} {b["y"]}" stroke="#0049b7" stroke-opacity=".2" fill="none"/>'
        )
    for node in nodes.values():
        x, y = node["x"], node["y"]
        anchor = "start" if x < 150 else "end" if x > 810 else "middle"
        radius = round(13 + math.sqrt(node["files"]) * 1.4, 1)
        parts += [
            f'<circle cx="{x}" cy="{y}" r="{radius + 9}" fill="none" stroke="#52616b" stroke-dasharray="2 6" opacity=".4"/>',
            f'<circle cx="{x}" cy="{y}" r="{radius}" fill="#faf8f2" stroke="#52616b"/>',
            f'<circle cx="{x}" cy="{y}" r="4" fill="#0049b7"/>',
            f'<text x="{x}" y="{y + radius + 27}" text-anchor="{anchor}" fill="#182b3b" stroke="#fffefa" stroke-width="3" paint-order="stroke" stroke-linejoin="round" font-family="sans-serif" font-size="15">{escape(node["label"])}</text>',
        ]
    return "\n".join([*parts, "</svg>", ""])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = build(ROOT / "src/roam")
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    svg = render_svg(data)
    svg_dest = DEST.with_name("atlas-map.svg")
    if args.check:
        if (
            not DEST.exists()
            or DEST.read_text(encoding="utf-8") != rendered
            or not svg_dest.exists()
            or svg_dest.read_text(encoding="utf-8") != svg
        ):
            raise SystemExit("Atlas snapshot differs; run scripts/build_atlas_data.py")
        print("Atlas snapshot matches current Python source")
    else:
        DEST.write_text(rendered, encoding="utf-8", newline="\n")
        svg_dest.write_text(svg, encoding="utf-8", newline="\n")
        print(f"Wrote {DEST}")
