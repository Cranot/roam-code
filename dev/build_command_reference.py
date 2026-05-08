"""auto-generate the complete command reference appendix.

The hand-curated workflow sections in
``templates/distribution/landing-page/docs/command-reference.html``
cover the most-used commands. This script appends a generated
"Complete reference" section listing every command with its short
help line, organised by category. Run after adding/removing CLI
commands::

    python dev/build_command_reference.py

It rewrites the appendix in-place between the markers
``<!-- BEGIN auto-reference -->`` and ``<!-- END auto-reference -->``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roam.cli import _CATEGORIES, _COMMANDS, _short_help_via_ast  # type: ignore[import-not-found]


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_appendix() -> str:
    lines: list[str] = []
    seen: set[str] = set()
    lines.append('<section class="section">')
    lines.append('  <h2 id="complete-reference">Complete Reference</h2>')
    lines.append("  <p>Auto-generated from <code>roam --help</code>. Every canonical command + alias.</p>")

    for category, names in _CATEGORIES.items():
        # Filter to canonical commands only; aliases share the help text.
        rows = []
        for name in names:
            if name not in _COMMANDS or name in seen:
                continue
            seen.add(name)
            help_text = _short_help_via_ast(name) or ""
            rows.append((name, help_text))
        if not rows:
            continue
        lines.append(f"  <h3>{_escape(category)}</h3>")
        lines.append('  <div class="table-wrap"><table>')
        lines.append("    <thead><tr><th>Command</th><th>Description</th></tr></thead>")
        lines.append("    <tbody>")
        for name, help_text in rows:
            lines.append(f"      <tr><td><code>roam {_escape(name)}</code></td><td>{_escape(help_text)}</td></tr>")
        lines.append("    </tbody></table></div>")

    # Catch any commands not assigned to a category.
    leftovers = [n for n in sorted(_COMMANDS) if n not in seen]
    if leftovers:
        lines.append("  <h3>Other</h3>")
        lines.append('  <div class="table-wrap"><table>')
        lines.append("    <thead><tr><th>Command</th><th>Description</th></tr></thead>")
        lines.append("    <tbody>")
        for name in leftovers:
            help_text = _short_help_via_ast(name) or ""
            lines.append(f"      <tr><td><code>roam {_escape(name)}</code></td><td>{_escape(help_text)}</td></tr>")
        lines.append("    </tbody></table></div>")

    lines.append("</section>")
    return "\n".join(lines)


def main() -> int:
    target = ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html"
    text = target.read_text(encoding="utf-8")
    appendix = _build_appendix()

    begin = "<!-- BEGIN auto-reference -->"
    end = "<!-- END auto-reference -->"
    if begin in text and end in text:
        new = re.sub(
            re.escape(begin) + r".*?" + re.escape(end),
            f"{begin}\n{appendix}\n{end}",
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
        target.write_text(new, encoding="utf-8")
        print(f"updated {target} ({len(_COMMANDS)} commands)")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
