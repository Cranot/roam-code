#!/usr/bin/env python3
"""Computed truth for roam-code's GitHub repository description.

The provider half of the ``dev/repo_description_drift.py`` contract: it
exposes ``truth() -> dict[str, int]`` mapping a *unit phrase* (as a human
would write it in a one-line description) to the number this repository can
actually prove. The drift checker owns the extract/compare/report half and
knows nothing about roam.

Every number here is derived, never typed:

* command / alias / category / MCP-tool / preset counts come from
  ``dev/build_readme_counts.collect_counts()`` — the exact function the
  in-tree README/AGENTS/llms-install/server-card gate already uses, so the
  description can never disagree with the README about what "281 commands"
  means;
* the language count is read from the ``_SUPPORTED_LANGUAGES`` literal in
  ``src/roam/languages/registry.py`` by AST, matching how
  ``roam.surface_counts`` reads every other count contract. (The README's
  "28 languages" is still a hand-written literal in the marker blocks; this
  provider deliberately does NOT copy that literal, so if the registry grows
  a language the description gate fires even though the README's own gate
  cannot yet see it.)

Stdlib-only and import-light: nothing here imports the parts of ``roam`` that
need third-party packages, so the CI job runs on a bare interpreter with no
dependency install.

Run it directly to see the map::

    python dev/description_truth.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_readme_counts():
    """Import ``dev/build_readme_counts.py`` by path.

    ``dev/`` is not a package, and the script is imported (not copied) on
    purpose — reusing ``collect_counts`` is what keeps this provider from
    becoming a second, silently-diverging implementation of the counting.
    """
    path = ROOT / "dev" / "build_readme_counts.py"
    name = "_roam_build_readme_counts"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations through
    # ``sys.modules[cls.__module__]`` on 3.12+, and an unregistered module
    # makes that lookup return None (AttributeError at class-creation time).
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def language_count(root: Path | None = None) -> int:
    """Count ``_SUPPORTED_LANGUAGES`` in ``src/roam/languages/registry.py``.

    AST rather than import: the registry module pulls in the tree-sitter
    stack at runtime, and a documentation gate must not need the parser
    toolchain installed to answer "how many languages do we claim".
    """
    root = root or ROOT
    path = root / "src" / "roam" / "languages" / "registry.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(isinstance(t, ast.Name) and t.id == "_SUPPORTED_LANGUAGES" for t in targets):
            continue
        value = node.value
        # ``frozenset({...})`` — the literal lives in the single call argument.
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            return len(value.elts)
        raise RuntimeError("_SUPPORTED_LANGUAGES is no longer a literal collection")
    raise RuntimeError("_SUPPORTED_LANGUAGES not found in src/roam/languages/registry.py")


def truth() -> dict[str, int]:
    """Unit phrase -> proven count, for the description drift gate.

    Keys are the phrasings a description would plausibly use. Number
    agreement is handled by the checker, so only one spelling is needed per
    unit. Extra keys cost nothing: a unit the description never mentions is
    simply never consulted.
    """
    # ``languages=`` is injected, not defaulted: ``collect_counts`` would
    # otherwise call ``_live_languages()``, which imports the tree-sitter
    # stack this provider promises (above) never to need — and whose result
    # this function discards anyway in favour of the AST count below.
    langs = language_count()
    counts = _load_build_readme_counts().collect_counts(ROOT, languages=langs)
    return {
        "commands": counts.command_names,
        "cli commands": counts.command_names,
        "canonical commands": counts.canonical_commands,
        "aliases": counts.alias_names,
        "command categories": counts.category_count,
        "categories": counts.category_count,
        "mcp tools": counts.mcp_full,
        "tools": counts.mcp_full,
        "core mcp tools": counts.mcp_core,
        "mcp tool presets": len(counts.mcp_presets),
        "languages": langs,
    }


if __name__ == "__main__":
    json.dump(truth(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
