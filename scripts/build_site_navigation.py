"""Keep the static site's primary navigation identical without runtime JavaScript.

Run with --write to update; the default is a read-only drift check.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "templates/distribution/landing-page"
NAV = re.compile(r'  <nav class="(?:site-nav|atlas-nav)" aria-label="Main navigation">.*?</nav>', re.S)
LINKS = (
    ("/explore", "Explore the map"),
    ("/docs/", "Docs"),
    ("/compare", "Compare"),
    ("/pricing", "Pricing"),
    ("https://github.com/Cranot/roam-code", "GitHub"),
    ("/setup", "Set up your agent"),
)


def navigation(relative_path: str) -> str:
    """Use page-current for exact destinations and section-current for child docs."""
    active = "/docs/" if relative_path.startswith("docs/") else "/" + relative_path.removesuffix(".html")
    items = []
    for href, label in LINKS:
        current = ""
        if href == active:
            current = (
                ' aria-current="true"'
                if relative_path.startswith("docs/") and relative_path != "docs/index.html"
                else ' aria-current="page"'
            )
        action = ' class="nav-start"' if href == "/setup" else ""
        items.append(f'        <li><a href="{href}"{action}{current}>{label}</a></li>')
    return (
        """  <nav class="site-nav" aria-label="Main navigation">
    <div class="nav-inner">
      <a href="/" class="site-logo" aria-label="Roam — home">
        <svg class="site-logo-mark" viewBox="0 0 32 32" fill="none" stroke="currentColor" aria-hidden="true" focusable="false">
          <line x1="16" y1="16" x2="0" y2="8" stroke-width="1" opacity="0.55"/>
          <line x1="16" y1="16" x2="32" y2="22" stroke-width="1" opacity="0.55"/>
          <line x1="16" y1="16" x2="16" y2="32" stroke-width="1" opacity="0.35"/>
          <circle cx="16" cy="16" r="4.5" fill="currentColor" stroke="none"/>
          <circle cx="16" cy="16" r="7.5" stroke-width="1" opacity="0.30"/>
        </svg>
        <span>roam<span class="logo-dot">.</span></span>
      </a>
      <input type="checkbox" id="nav-toggle" class="nav-toggle-checkbox" aria-label="Toggle navigation menu" aria-controls="site-navigation">
      <label for="nav-toggle" class="nav-toggle-label">
        <span class="nav-burger" aria-hidden="true"></span><span class="visually-hidden">Menu</span>
      </label>
      <ul class="nav-links" id="site-navigation">
"""
        + "\n".join(items)
        + """
      </ul>
    </div>
  </nav>"""
    )


def updated(source: str, relative_path: str) -> str:
    if len(NAV.findall(source)) != 1:
        raise ValueError(f"{relative_path}: expected exactly one primary navigation")
    return NAV.sub(lambda _: navigation(relative_path), source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    paths = sorted(SITE.rglob("*.html"))
    if not paths:
        raise ValueError("No site pages scanned")
    # Validate the whole corpus before changing any page.
    changes = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        result = updated(source, path.relative_to(SITE).as_posix())
        if source != result:
            changes.append((path, result))
    for path, result in changes:
        print(path.relative_to(SITE))
        if args.write:
            path.write_text(result, encoding="utf-8", newline="\n")
    print(f"Scanned {len(paths)} pages; {len(changes)} {'updated' if args.write else 'with drift'}.")
    return 0 if args.write or not changes else 1


if __name__ == "__main__":
    raise SystemExit(main())
