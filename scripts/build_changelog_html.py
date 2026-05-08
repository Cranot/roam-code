#!/usr/bin/env python3
"""Render CHANGELOG.md into the body of changelog.html.

The landing-page changelog page is just the surrounding navigation /
header / footer template; the body comes from CHANGELOG.md so we
have a single source of truth. Drift between the two used to produce
"changelog says 12.46 latest, README says 12.49" inconsistencies.

Usage:
    python scripts/build_changelog_html.py            # dry-run (report drift)
    python scripts/build_changelog_html.py --write    # rewrite the page in place

CI usage:
    python scripts/build_changelog_html.py            # exit 1 if drift detected

Markdown handled (intentionally minimal — matches the existing
landing-page CSS classes, no external dep):

* ``## [vN.N] - YYYY-MM-DD`` → ``<h2>[vN.N] - YYYY-MM-DD</h2>``
* ``### Heading`` → ``<h3>Heading</h3>``
* ``#### Heading`` → ``<h4>Heading</h4>``
* ``- bullet`` → ``<li>bullet</li>`` (consecutive bullets group into ``<ul>``)
* Blank line → paragraph break
* Other prose lines → ``<p>line</p>``
* Inline: ``**bold**``, ``*ital*``, `` `code` ``, ``[text](url)``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_MD = REPO_ROOT / "CHANGELOG.md"
CHANGELOG_HTML = REPO_ROOT / "templates" / "distribution" / "landing-page" / "changelog.html"

# Markers inside changelog.html that bracket the rendered body. The
# template (header, footer, navigation) lives outside them. If absent,
# the script injects them on first run.
BEGIN_MARKER = "<!-- BEGIN auto-changelog -->"
END_MARKER = "<!-- END auto-changelog -->"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_INLINE_CODE = re.compile(r"``(.+?)``|`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline(text: str) -> str:
    """Inline-format a single line of prose."""
    text = _escape(text)
    # Code: prefer double-backtick span first so single-backtick inside
    # doesn't get re-matched.
    text = _INLINE_CODE.sub(
        lambda m: f"<code>{m.group(1) or m.group(2)}</code>",
        text,
    )
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    return text


def render_markdown(md: str) -> str:
    """Render the Keep-a-Changelog-shaped markdown into HTML body."""
    lines = md.splitlines()
    out: list[str] = []
    in_ul = False

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Empty line → close any open list, then skip
        if not stripped:
            close_ul()
            i += 1
            continue

        # Headings
        if stripped.startswith("# "):
            close_ul()
            # Skip top-level "# Changelog" — the page already has its own h1.
            i += 1
            continue
        if stripped.startswith("## "):
            close_ul()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            i += 1
            continue
        if stripped.startswith("### "):
            close_ul()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
            i += 1
            continue
        if stripped.startswith("#### "):
            close_ul()
            out.append(f"<h4>{_inline(stripped[5:])}</h4>")
            i += 1
            continue

        # Bullets: ``- ``, ``* ``. Continuation lines indented by ≥2
        # spaces are part of the same bullet.
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            content = stripped[2:]
            # Pull continuation lines into this bullet.
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    break
                if nxt.lstrip() != nxt and not (nxt.lstrip().startswith("- ") or nxt.lstrip().startswith("* ")):
                    content += " " + nxt.strip()
                    j += 1
                else:
                    break
            out.append(f"<li>{_inline(content)}</li>")
            i = j
            continue

        # Plain prose: greedily collect consecutive non-empty,
        # non-heading, non-bullet lines into one paragraph. This is
        # the standard markdown paragraph rule and produces cleaner
        # HTML than one ``<p>`` per source line (which the prior
        # hand-rolled changelog.html used and which read awkwardly).
        close_ul()
        para_lines = [stripped]
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if not nxt:
                break
            if nxt.startswith(("#", "- ", "* ")):
                break
            para_lines.append(nxt)
            j += 1
        out.append(f"<p>{_inline(' '.join(para_lines))}</p>")
        i = j

    close_ul()
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Splice into changelog.html
# ---------------------------------------------------------------------------


def splice_into_template(template: str, rendered_body: str) -> str:
    """Replace content between BEGIN/END markers; inject markers on first run.

    Uses string slicing instead of ``re.sub`` for the replacement so any
    backslashes (``\\w``, ``\\d``, etc.) in rendered changelog content
    don't get interpreted as regex backreferences in the substitution.
    """
    new_block = f"{BEGIN_MARKER}\n{rendered_body}{END_MARKER}"

    if BEGIN_MARKER in template and END_MARKER in template:
        begin_idx = template.index(BEGIN_MARKER)
        end_idx = template.index(END_MARKER) + len(END_MARKER)
        return template[:begin_idx] + new_block + template[end_idx:]

    # First run — inject between ``<article>`` and ``</article>``.
    if "<article>" not in template or "</article>" not in template:
        raise SystemExit(
            "changelog.html has no <article> tag and no auto-changelog markers; "
            "cannot determine where to inject the rendered body."
        )
    open_idx = template.index("<article>") + len("<article>")
    close_idx = template.index("</article>")
    head = template[:open_idx]
    tail = template[close_idx:]
    return f"{head}\n{new_block}\n  {tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Rewrite changelog.html in place (default: dry-run)")
    args = ap.parse_args()

    if not CHANGELOG_MD.exists():
        print(f"ERROR: {CHANGELOG_MD} missing", file=sys.stderr)
        return 1
    if not CHANGELOG_HTML.exists():
        print(f"ERROR: {CHANGELOG_HTML} missing", file=sys.stderr)
        return 1

    md = CHANGELOG_MD.read_text(encoding="utf-8")
    template = CHANGELOG_HTML.read_text(encoding="utf-8")

    rendered_body = render_markdown(md)
    new_template = splice_into_template(template, rendered_body)

    if new_template == template:
        print("changelog.html is in sync with CHANGELOG.md.")
        return 0

    # Show a tight diff summary so dry-run is informative.
    before_versions = re.findall(r"<h2>\[([^\]]+)\]", template)
    after_versions = re.findall(r"<h2>\[([^\]]+)\]", new_template)
    print(f"Drift detected:")
    print(f"  CHANGELOG.md latest entries: {after_versions[:5]}")
    print(f"  changelog.html currently:    {before_versions[:5]}")

    if args.write:
        CHANGELOG_HTML.write_text(new_template, encoding="utf-8")
        print(f"  -> wrote {CHANGELOG_HTML.relative_to(REPO_ROOT)}")
        return 0
    print("Run with --write to rewrite changelog.html.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
