"""auto-generate the complete command reference appendix.

The hand-curated workflow sections in
``templates/distribution/landing-page/docs/command-reference.html``
cover the most-used commands. This script appends a generated
"Complete reference" section listing every command with its complete
first docstring paragraph, organised by category. Command source is
read via AST, including same-module function aliases, never imported.
Run after adding/removing CLI commands or changing their summaries::

    python dev/build_command_reference.py

It rewrites the appendix in-place between the markers
``<!-- BEGIN auto-reference -->`` and ``<!-- END auto-reference -->``.
"""

from __future__ import annotations

import ast
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roam.cli import _CATEGORIES, _COMMANDS, _DEPRECATED_COMMANDS  # type: ignore[import-not-found]

ModuleBindings = dict[str, str | ast.Name | None]


def _escape(text: str) -> str:
    return escape(text, quote=True)


def _module_bindings(source: Path) -> ModuleBindings:
    """Read top-level functions and static aliases without executing source."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    # Retain only summaries and alias names, not every command's function body.
    bindings: ModuleBindings = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings[node.name] = ast.get_docstring(node) or ""
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value if isinstance(node.value, ast.Name) else None
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            bindings[node.target.id] = node.value if isinstance(node.value, ast.Name) else None
    return bindings


def _command_description(name: str, modules: dict[str, ModuleBindings]) -> str:
    """Resolve the registered function's first paragraph, with no CLI cutoff."""
    module_name, attribute = _COMMANDS[name]
    source = ROOT / "src" / Path(*module_name.split(".")).with_suffix(".py")
    try:
        if module_name not in modules:
            modules[module_name] = _module_bindings(source)
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ValueError(f"Cannot describe command '{name}': unable to parse {source}: {exc}") from exc

    bindings = modules[module_name]
    visited: set[str] = set()
    while attribute not in visited:
        visited.add(attribute)
        binding = bindings.get(attribute)
        if isinstance(binding, str):
            paragraph = re.split(r"\n\s*\n", binding.strip(), maxsplit=1)[0]
            description = " ".join(paragraph.split())
            if description:
                return description
            break
        if not isinstance(binding, ast.Name):
            break
        attribute = binding.id
    raise ValueError(
        f"Cannot describe command '{name}': no static function docstring for {_COMMANDS[name]} in {source}"
    )


def _command_row(name: str, description: str) -> str:
    anchor = "command-" + _escape(name)
    description_html = _escape(description)
    alias = _DEPRECATED_COMMANDS.get(name)
    if alias:
        replacement = alias["replacement"] if isinstance(alias, dict) else alias
        description_html += (
            f' Legacy alias of <a href="#command-{_escape(replacement)}"><code>roam {_escape(replacement)}</code></a>.'
        )
    return (
        f'      <tr id="{anchor}"><td><a href="#{anchor}"><code>roam {_escape(name)}</code></a></td>'
        f"<td>{description_html}</td></tr>"
    )


def _category_table(category: str, names: list[str], modules: dict[str, ModuleBindings]) -> list[str]:
    return [
        f"  <h3>{_escape(category)}</h3>",
        '  <div class="table-wrap" tabindex="0" role="group" aria-label="Scrollable reference table"><table>',
        "    <thead><tr><th>Command</th><th>Description</th></tr></thead>",
        "    <tbody>",
        *(_command_row(name, _command_description(name, modules)) for name in names),
        "    </tbody></table></div>",
    ]


def _build_appendix() -> str:
    lines: list[str] = []
    seen: set[str] = set()
    modules: dict[str, ModuleBindings] = {}
    lines.append('<section class="section">')
    lines.append('  <h2 id="complete-reference">Complete Reference</h2>')
    lines.append(
        "  <p>Auto-generated from the CLI command registry and complete first docstring paragraphs. "
        "Every canonical command + alias has a direct link; "
        "inspect the same surface with <code>roam --help-all</code>.</p>"
    )

    for category, names in _CATEGORIES.items():
        # Keep every registered name, including aliases, once across categories.
        rows = []
        for name in names:
            if name not in _COMMANDS or name in seen:
                continue
            seen.add(name)
            rows.append(name)
        if not rows:
            continue
        lines.extend(_category_table(category, rows, modules))

    # Catch any commands not assigned to a category.
    leftovers = [n for n in sorted(_COMMANDS) if n not in seen]
    if leftovers:
        lines.extend(_category_table("Other", leftovers, modules))

    lines.append("</section>")
    return "\n".join(lines)


def main() -> int:
    target = ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html"
    # Preserve surrounding handwritten bytes, including mixed line endings.
    with target.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()

    begin = "<!-- BEGIN auto-reference -->"
    end = "<!-- END auto-reference -->"
    counts = (text.count(begin), text.count(end))
    if counts == (0, 0):
        has_markers = False
    elif counts == (1, 1) and text.index(begin) < text.index(end):
        has_markers = True
    else:
        print(
            "ERROR: expected one ordered pair of auto-reference markers, or no markers for first generation",
            file=sys.stderr,
        )
        return 1

    try:
        appendix = _build_appendix()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if has_markers:
        new = re.sub(
            re.escape(begin) + r".*?" + re.escape(end),
            lambda _match: f"{begin}\n{appendix}\n{end}",
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        # First run — inject before </main>.
        if "</main>" not in text:
            print("ERROR: no </main> tag in command-reference.html", file=sys.stderr)
            return 1
        injection = f"\n{begin}\n{appendix}\n{end}\n"
        new = text.replace("</main>", injection + "</main>", 1)

    if new != text:
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(new)
        print(f"updated {target} ({len(_COMMANDS)} commands)")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
