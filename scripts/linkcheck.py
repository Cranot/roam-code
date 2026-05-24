#!/usr/bin/env python3
"""Internal-link integrity for the landing-page tree.

Walks every tracked HTML file under templates/distribution/landing-page/
and asserts every internal href resolves to either:

  - A real .html file (or directory with index.html) under that tree.
  - A real id= anchor on the linked page.

External URLs (http://, https://, mailto:) are not checked. Run with
--external to also probe external URLs (slow, network-bound).

Usage:
    python scripts/linkcheck.py            # internal links only
    python scripts/linkcheck.py --external # also check external 200/302/3xx
    python scripts/linkcheck.py --strict   # exit 1 on any issue (CI mode)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "templates" / "distribution" / "landing-page"


def _resolve_target(href: str, current_page: str) -> tuple[str, str | None]:
    """Return (target_path, anchor_or_none) given an href and the page it's on."""
    if href.startswith("#"):
        return current_page, href[1:]
    if "#" in href:
        base, _, anchor = href.partition("#")
    else:
        base, anchor = href, None
    return base, anchor


def _path_exists_on_site(path: str) -> Path | None:
    """Return the resolved .html file Path (or None if 404)."""
    if path == "/" or path == "":
        return SITE / "index.html"
    p = path.lstrip("/")
    # /docs/ → docs/index.html
    candidates = [SITE / p, SITE / (p + ".html"), SITE / p / "index.html"]
    if p.endswith("/"):
        candidates.append(SITE / p / "index.html")
        candidates.append(SITE / (p.rstrip("/") + ".html"))
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _collect_ids(page: Path) -> set[str]:
    """Return every id= attribute value on the page (any tag)."""
    text = page.read_text(encoding="utf-8")
    return set(re.findall(r'\bid="([^"]+)"', text))


def _parse_args() -> argparse.Namespace:
    """Build the CLI parser and return parsed args."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--external", action="store_true", help="Also check external URLs (slow)")
    ap.add_argument("--strict", action="store_true", help="Exit 1 on any issue (CI mode)")
    return ap.parse_args()


def _discover_pages() -> list[Path]:
    """Return every landing-page HTML file we want to audit.

    Skips ``changelog.html`` -- it is auto-rendered from CHANGELOG.md and
    necessarily contains example markdown link references (``[path](path)``
    etc.) that aren't real navigation. The changelog-render gate covers
    content correctness via its own source-of-truth diff against CHANGELOG.md.
    """
    skip = {"changelog.html"}
    candidates = list(SITE.glob("*.html")) + list(SITE.glob("docs/*.html"))
    return [p for p in candidates if p.name not in skip]


def _scan_internal_links(pages: list[Path], page_ids: dict[Path, set[str]]) -> tuple[list[str], list[str]]:
    """Walk every ``<a href>`` in each page, returning (issues, external_urls)."""
    issues: list[str] = []
    external_to_check: list[str] = []
    for page in pages:
        rel = page.relative_to(SITE).as_posix()
        text = page.read_text(encoding="utf-8")
        for m in re.finditer(r'<a [^>]*href="([^"]+)"', text):
            href = m.group(1)
            if href.startswith("mailto:") or href.startswith("tel:"):
                continue
            if href.startswith("http://") or href.startswith("https://"):
                external_to_check.append(href)
                continue
            base, anchor = _resolve_target(href, rel)
            target = _path_exists_on_site(base)
            if target is None:
                issues.append(f"{rel}: 404 → {href} (base: {base})")
                continue
            if anchor:
                ids = page_ids.get(target) or _collect_ids(target)
                page_ids[target] = ids
                if anchor not in ids:
                    issues.append(
                        f"{rel}: missing #{anchor} on {target.relative_to(SITE).as_posix()} (full href: {href})"
                    )
    return issues, external_to_check


def _check_external_urls(urls: list[str]) -> list[str]:
    """HEAD-probe each unique URL; return issue strings for failures."""
    try:
        import urllib.request
    except ImportError:
        print("urllib not available; skipping external check", file=sys.stderr)
        return []
    issues: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status >= 400:
                    issues.append(f"external: {url} returned {r.status}")
        except Exception as e:
            issues.append(f"external: {url} ({e.__class__.__name__})")
    return issues


def _render_report(pages: list[Path], issues: list[str], strict: bool) -> int:
    """Print summary + first 50 issues; return the appropriate exit code."""
    print(f"Checked {len(pages)} pages.")
    if not issues:
        print("All internal links resolve.")
        return 0
    print(f"\n{len(issues)} issue(s):")
    for i in issues[:50]:
        print(f"  {i}")
    if len(issues) > 50:
        print(f"  ... and {len(issues) - 50} more")
    return 1 if strict else 0


def main() -> int:
    args = _parse_args()

    if not SITE.exists():
        print(f"Site dir not found: {SITE}", file=sys.stderr)
        return 2

    pages = _discover_pages()
    page_ids: dict[Path, set[str]] = {p: _collect_ids(p) for p in pages}

    issues, external_to_check = _scan_internal_links(pages, page_ids)

    if args.external:
        issues.extend(_check_external_urls(external_to_check))

    return _render_report(pages, issues, args.strict)


if __name__ == "__main__":
    sys.exit(main())
